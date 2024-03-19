# -*- coding: utf-8 -*-
"""RNN.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/13XJVI6_SJO5WLVdO5uP2qzQ0y_isjAzA
"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

import glob
import time
import json
import struct
import base64
import argparse
import tempfile 
import dill
import tensorflow as tf

VAL_PERCENT = 20

print(tf.test.gpu_device_name())
config = tf.ConfigProto()
config.gpu_options.allow_growth = True


def parse_args():
    parser = argparse.ArgumentParser("Entry script to launch training")
    parser.add_argument("--config-path", type=str, default = "./config.json", help="Path to the config file")
    parser.add_argument("--data-dir", type=str, default = "./data", help="Path to the data directory")
    parser.add_argument("--output-path", type=str, default = "output.json", help="Path to the output file")
    parser.add_argument("--pretrained-checkpoint-dir", type=str, default = None, help="Path to the pretrained checkpoint directory")
    return parser.parse_args()

def get_file_content(file_path):
  with open(file_path, "r") as f:
    data = f.read()
  return data


def write_to_file(file_path, content):
    with open(file_path, "w") as f:
        f.write(content)

def create_dataset_from_text(text, batch_size, seq_length, val_percent=VAL_PERCENT):
  # The unique characters in the file
  vocab = sorted(set(text))

  ids_from_chars = tf.keras.layers.StringLookup(vocabulary=list(vocab), mask_token=None)

  chars_from_ids = tf.keras.layers.StringLookup(
      vocabulary=ids_from_chars.get_vocabulary(), invert=True, mask_token=None)
  
  def text_from_ids(ids):
    return tf.strings.reduce_join(chars_from_ids(ids), axis=-1)

  all_ids = ids_from_chars(tf.strings.unicode_split(text, 'UTF-8'))

  val_start = int(len(all_ids) * (1 - val_percent/100))
  train_ids, val_ids = all_ids[:val_start], all_ids[val_start:]

  def get_dataset(all_ids):
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
    
    return dataset
  
  # train_ds, val_ds = tf.keras.utils.split_dataset(dataset, left_size=1-val_percent/100, shuffle=True, seed=shuffle_seed)
  train_ds = get_dataset(train_ids)
  val_ds = get_dataset(val_ids)

  return train_ds, val_ds, chars_from_ids, ids_from_chars, text_from_ids

def build_model(vocab_size, embedding_dim, rnn_units, batch_size):
    model = tf.keras.models.Sequential()
    
    model.add(tf.keras.layers.Embedding(
        input_dim=vocab_size,
        output_dim=embedding_dim,
        batch_input_shape=[batch_size, None]
    ))
    
    model.add(tf.keras.layers.LSTM(
        units=rnn_units,
        return_sequences=True,
        stateful=True,
    ))
    
    model.add(tf.keras.layers.Dense(vocab_size))
    
    return model

class OneStep():
    def __init__(self, model, chars_from_ids, ids_from_chars, temperature=1.0):
        self.temperature = temperature
        self.model = model
        self.chars_from_ids = chars_from_ids
        self.ids_from_chars = ids_from_chars

        # Create a mask to prevent "[UNK]" from being generated.
        skip_ids = self.ids_from_chars(['[UNK]'])[:, None]
        sparse_mask = tf.SparseTensor(
            # Put a -inf at each bad index.
            values=[-float('inf')] * len(skip_ids),
            indices=skip_ids,
            # Match the shape to the vocabulary
            dense_shape=[len(ids_from_chars.get_vocabulary())])
        self.prediction_mask = tf.sparse.to_dense(sparse_mask)

    def generate_one_step(self, inputs):
        # Convert strings to token IDs.
        input_chars = tf.strings.unicode_split(inputs, 'UTF-8')
        input_ids = self.ids_from_chars(input_chars).to_tensor()

        # Run the model.
        # predicted_logits.shape is [batch, char, next_char_logits]
        predicted_logits = self.model(inputs=input_ids)
        # Only use the last prediction.
        predicted_logits = predicted_logits[:, -1, :]

        # print(predicted_logits)

        predicted_logits = predicted_logits / self.temperature
        # Apply the prediction mask: prevent "[UNK]" from being generated.
        predicted_logits = predicted_logits + self.prediction_mask

        # Sample the output logits to generate token IDs.
        predicted_ids = tf.random.categorical(predicted_logits, num_samples=1)
        predicted_ids = tf.squeeze(predicted_ids, axis=-1)

        # Convert from token ids to characters
        predicted_chars = self.chars_from_ids(predicted_ids)

        # Return the characters and model state.
        return predicted_chars

def get_model(vocab_size, embedding_dim, rnn_units, batch_size, pretrained_checkpoint_dir):
    model = build_model(vocab_size, embedding_dim, rnn_units, batch_size)
    # load pretrained weights
    if pretrained_checkpoint_dir is not None:
        model.load_weights(tf.train.latest_checkpoint(pretrained_checkpoint_dir))
    loss = tf.losses.SparseCategoricalCrossentropy(from_logits=True)
    model.compile(optimizer='adam', loss=loss)
    return model

def train_model(model, train_ds, val_ds, checkpoint_dir, epochs):
    # Name of the checkpoint files
    early_stopping = tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=3)
    checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
        filepath = os.path.join(checkpoint_dir, "best.hdf5"),
        save_weights_only=True,
        save_best_only=True,  # Only save the best model based on validation loss
        monitor='val_loss',
        mode='min'
    )
    with tf.device('/gpu:0'):
        model.fit(train_ds, epochs=epochs, validation_data=val_ds, callbacks=[checkpoint_callback, early_stopping])

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

def test_model(model, chars_from_ids, ids_from_chars):
    one_step_model = OneStep(model, chars_from_ids, ids_from_chars)

    start = time.time()

    next_char = tf.constant(['Q'])
    result = [next_char]

    for _ in range(1000):
        next_char = one_step_model.generate_one_step(next_char)
        result.append(next_char)

    result = tf.strings.join(result)

    end = time.time()
    print('\nRun time:', end - start)    

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

def main():
    args = parse_args()
    # The embedding dimension
    config_path = args.config_path
    data_dir = args.data_dir
    output_path = args.output_path

    with open(config_path, "r") as f:
        config = json.load(f)
    embedding_dim = config["embedding_dim"]
    rnn_units = config["rnn_units"]
    batch_size = config["batch_size"]
    seq_length = config["seq_length"]
    epochs = config["epoch_num"]

    pretrained_checkpoint_dir = args.pretrained_checkpoint_dir

    checkpoint_dir = './checkpoints'
    datasets = glob.glob(os.path.join(data_dir, "*"))
    text = ""
    for dataset in datasets:
      text += get_text_from_dataset(dataset)
      text += "\n"
  
    train_ds, val_ds, chars_from_ids, ids_from_chars, text_from_ids = create_dataset_from_text(text, batch_size, seq_length)
    # with open(os.path.join(), 'wb') as f:
    #     dill.dump(ids_from_chars, f)
    
    vocab_size = len(ids_from_chars.get_vocabulary())
    model = get_model(vocab_size, embedding_dim, rnn_units, batch_size, pretrained_checkpoint_dir)

    train_model(model, train_ds, val_ds, checkpoint_dir, epochs)

    model.summary()

    model.load_weights(tf.train.latest_checkpoint(checkpoint_dir))
    weight_base64, compressed_config = get_model_for_export(model)

    inscription = {
        "model_name": "RNN",
        "layers_config": compressed_config,
        "vocabulary": ids_from_chars.get_vocabulary(),
        "weight_b64": weight_base64
    }
    inscription_json = json.dumps(inscription)
    write_to_file(output_path, inscription_json)
    

if __name__ == "__main__":
    main()