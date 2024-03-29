# -*- coding: utf-8 -*-
"""RNN.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/13XJVI6_SJO5WLVdO5uP2qzQ0y_isjAzA
"""

import tensorflow as tf
import os
import time
import json
import argparse
import glob


def parse_args():
    parser = argparse.ArgumentParser("Entry script to launch inference")
    parser.add_argument("--config-path", type=str, default = "./config.json", help="Path to the config file")
    parser.add_argument("--data-dir", type=str, default = "./data", help="Path to the data directory")
    parser.add_argument("--checkpoint-path", type =str, required = True, help="Path to the checkpoint file")
    return parser.parse_args()

def get_file_content(file_path):
  with open(file_path, "r") as f:
    data = f.read()
  return data


def write_to_file(file_path, content):
  f = open(file_path, "w")
  f.write(content)
  f.close()


def get_text_from_dataset(dir):
  data_paths = glob.glob(os.path.join(dir, "*.txt"))
  def get_text_from_file(file_path):
      # Read, then decode for py2 compat.
      text = open(file_path, 'rb').read().decode(encoding='utf-8')
      return text
  text = ""
  for data_path in data_paths:
      text += get_text_from_file(data_path)
      text += "\n"
  return text

import struct
import base64

def compressConfig(data):
    layers = []
    for layer in data["config"]["layers"]:
        cfg = layer["config"]
        layer_config = None
        if layer["class_name"] == "InputLayer":
            layer_config = {
                "batch_input_shape": cfg["batch_input_shape"]
            }
        elif layer["class_name"] == "Rescaling":
            layer_config = {
                "scale": cfg["scale"],
                "offset": cfg["offset"]
            }
        elif layer["class_name"] == "Dense":
            layer_config = {
                "units": cfg["units"],
                "activation": cfg["activation"]
            }
        elif layer["class_name"] == "Conv2D":
            layer_config = {
                "filters": cfg["filters"],
                "kernel_size": cfg["kernel_size"],
                "strides": cfg["strides"],
                "activation": cfg["activation"],
                "padding": cfg["padding"]
            }
        elif layer["class_name"] == "MaxPooling2D":
            layer_config = {
                "pool_size": cfg["pool_size"],
                "strides": cfg["strides"],
                "padding": cfg["padding"]
            }
        elif layer["class_name"] == "Embedding":
            layer_config = {
                "input_dim": cfg["input_dim"],
                "output_dim": cfg["output_dim"]
            }
        elif layer["class_name"] == "SimpleRNN":
            layer_config = {
                "units": cfg["units"],
                "activation": cfg["activation"]
            }
        elif layer["class_name"] == "LSTM":
            layer_config = {
                "units": cfg["units"],
                "activation": cfg["activation"],
                "recurrent_activation": cfg["recurrent_activation"]
            }
        res_layer = {
            "class_name": layer["class_name"],
        }
        if layer_config is not None:
            res_layer["config"] = layer_config
        layers.append(res_layer)

    return {
        "config": {
            "layers": layers
        }
    }

def get_model_for_export(model):
    weight_np = model.get_weights()

    weight_bytes = bytearray()
    for idx, layer in enumerate(weight_np):
        # write_to_file(os.path.join(model_output_dir, f"model_weight_{idx:02}.txt"), str(layer))
        flatten = layer.reshape(-1).tolist()
        flatten_packed = map(lambda i: struct.pack("@f", i), flatten)
        for i in flatten_packed:
            weight_bytes.extend(i)

    weight_base64 = base64.b64encode(weight_bytes).decode()
    config = json.loads(model.to_json())
    compressed_config = compressConfig(config)
    return weight_base64, compressed_config


def create_dataset_from_text(text, batch_size, seq_length):
  # The unique characters in the file
  vocab = sorted(set(text))

  print(vocab)

  ids_from_chars = tf.keras.layers.StringLookup(vocabulary=list(vocab), mask_token=None)

  chars_from_ids = tf.keras.layers.StringLookup(
      vocabulary=ids_from_chars.get_vocabulary(), invert=True, mask_token=None)

  def text_from_ids(ids):
    return tf.strings.reduce_join(chars_from_ids(ids), axis=-1)

  all_ids = ids_from_chars(tf.strings.unicode_split(text, 'UTF-8'))

  ids_dataset = tf.data.Dataset.from_tensor_slices(all_ids)

  sequences = ids_dataset.batch(seq_length+1, drop_remainder=True)

  def split_input_target(sequence):
      input_text = sequence[:-1]
      target_text = sequence[1:]
      return input_text, target_text

  dataset = sequences.map(split_input_target)

  # Buffer size to shuffle the dataset
  # (TF data is designed to work with possibly infinite sequences,
  # so it doesn't attempt to shuffle the entire sequence in memory. Instead,
  # it maintains a buffer in which it shuffles elements).
  BUFFER_SIZE = 10000

  dataset = (
      dataset
      .shuffle(BUFFER_SIZE)
      .batch(batch_size, drop_remainder=True)
      .prefetch(tf.data.experimental.AUTOTUNE))
  
  return dataset, chars_from_ids, ids_from_chars, text_from_ids



class OneStep(tf.keras.Model):
  def __init__(self, model, chars_from_ids, ids_from_chars, temperature=1.0):
    super().__init__()
    self.temperature = temperature
    self.model = model
    self.chars_from_ids = chars_from_ids
    self.ids_from_chars = ids_from_chars

    # Create a mask to prevent "[UNK]" from being generated.
    skip_ids = self.ids_from_chars(['[UNK]'])[:, None]
    sparse_mask = tf.SparseTensor(
        # Put a -inf at each bad index.
        values=[-float('inf')]*len(skip_ids),
        indices=skip_ids,
        # Match the shape to the vocabulary
        dense_shape=[len(ids_from_chars.get_vocabulary())])
    self.prediction_mask = tf.sparse.to_dense(sparse_mask)

  @tf.function
  def generate_one_step(self, inputs):
    # Convert strings to token IDs.
    input_chars = tf.strings.unicode_split(inputs, 'UTF-8')
    input_ids = self.ids_from_chars(input_chars).to_tensor()
    
    # Run the model.
    # predicted_logits.shape is [batch, char, next_char_logits]
    predicted_logits = self.model(inputs=input_ids)

    # Only use the last prediction.
    predicted_logits = predicted_logits[:, -1, :]
    predicted_logits = predicted_logits/self.temperature
    # Apply the prediction mask: prevent "[UNK]" from being generated.
    predicted_logits = predicted_logits + self.prediction_mask

    # Sample the output logits to generate token IDs.
    predicted_ids = tf.random.categorical(predicted_logits, num_samples=1)
    predicted_ids = tf.squeeze(predicted_ids, axis=-1)

    # Convert from token ids to characters
    predicted_chars = self.chars_from_ids(predicted_ids)

    # Return the characters and model state.
    return predicted_chars



def train_model(model, dataset, checkpoint_dir, epochs):
  # Name of the checkpoint files
  checkpoint_prefix = os.path.join(checkpoint_dir, "ckpt_{epoch}")

  checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
      filepath=checkpoint_prefix,
      save_weights_only=True
  )

  model.fit(dataset, epochs=epochs, callbacks=[checkpoint_callback])


def test_model(model, chars_from_ids, ids_from_chars, prompt, temperature=1.0):
    # with open('/Users/vuonggiahuy/rnn-training-2/data/shakepeare1/shakespeare.json', 'r') as f:
    #    collection = json.load(f)
    one_step_model = OneStep(model, chars_from_ids, ids_from_chars, temperature)

    start = time.time()
    next_char = tf.constant([prompt for _ in range(1024)])
    result = [next_char]
    word = ""

    for n in range(1000):
        next_char = one_step_model.generate_one_step(next_char)
        result.append(next_char)

    result = tf.strings.join(result)

    end = time.time()
    context = result[-1].numpy().decode('utf-8')
    print(context)
    print('\nRun time:', end - start)


def main():
    args = parse_args()
    # The embedding dimension
    config_path = args.config_path
    data_dir = args.data_dir

    with open(config_path, "r") as f:
        config = json.load(f)

    batch_size = config["batch_size"]
    seq_length = config["seq_length"]

    temperature = 0.7
    prompt = 'Harry'
    # prompt = ''

    datasets = glob.glob(os.path.join(data_dir, "*"))
    text = ""
    for dataset in datasets:
        text += get_text_from_dataset(dataset)
        text += "\n"

    dataset, chars_from_ids, ids_from_chars, text_from_ids = create_dataset_from_text(text, batch_size, seq_length)

    # Length of the vocabulary in StringLookup Layer
    
    model = tf.keras.models.load_model(args.checkpoint_path)
    print(model.summary())
    test_model(model, chars_from_ids, ids_from_chars, prompt, temperature)

if __name__ == "__main__":
    main()