# -*- coding: utf-8 -*-
"""RNN.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/13XJVI6_SJO5WLVdO5uP2qzQ0y_isjAzA
"""

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 

import glob
import json
import struct
import base64
import pickle
import argparse
from tqdm import tqdm
import numpy as np 
import tensorflow as tf

def parse_args():
    parser = argparse.ArgumentParser("Entry script to launch training")
    parser.add_argument("--config-path", type=str, default = "./config.json", help="Path to the config file")
    parser.add_argument("--data-dir", type=str, default = "./data", help="Path to the data directory")
    parser.add_argument("--output-dir", type=str, default = "./output", help="Path to the output directory")
    parser.add_argument("--checkpoint-path", type =str, default=None, help="Path to the checkpoint file")
    return parser.parse_args()

def get_file_content(file_path):
  with open(file_path, "r") as f:
    data = f.read()
  return data


def write_to_file(file_path, content):
    with open(file_path, "w") as f:
        f.write(content)

def create_dataset_from_text(text_list, seq_length):
    # The unique characters in the file
    vocab = sorted(set(item for item in text_list))
    # add [UNK] token
    vocab.append("[UNK]")
    vocab_to_index = dict((note, number) for number, note in enumerate(vocab)) 

    inputs = []
    targets = []
    # create input sequences and the corresponding outputs
    for i in range(0, len(text_list) - seq_length):
        sequence_in = text_list[i:i + seq_length]
        sequence_out = text_list[i + seq_length]
        inputs.append([vocab_to_index[char] for char in sequence_in])
        targets.append([vocab_to_index[sequence_out]])
    
    # reshape the input into a format compatible with LSTM layers
    inputs = np.reshape(inputs, (len(inputs), seq_length, 1))/len(vocab)
    targets = np.array(targets)  
    
    return inputs, targets, vocab_to_index

def create_model(config, model_path = None):
    if model_path is not None:
        model = tf.keras.models.load_model(model_path)
        return model
    
    embedding_dim = config["embedding_dim"]
    rnn_units = config["rnn_units"]
    vocab_size = config["vocab_size"]
    sequence_length = config["seq_length"]

    model = tf.keras.models.Sequential([
        tf.keras.layers.InputLayer(input_shape=(sequence_length, 1)),
        tf.keras.layers.LSTM(units=rnn_units),
        tf.keras.layers.Dense(vocab_size)
    ])
    # load pretrained weight
    loss = tf.losses.SparseCategoricalCrossentropy(from_logits=True)
    optimizer = tf.optimizers.Adam(learning_rate=0.001)
    model.compile(optimizer = optimizer, loss = loss, metrics=['accuracy'])

    model.summary()
    return model

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
        flatten = layer.reshape(-1).tolist()
        flatten_packed = map(lambda i: struct.pack("@f", i), flatten)
        for i in flatten_packed:
            weight_bytes.extend(i)

    weight_base64 = base64.b64encode(weight_bytes).decode()
    config = json.loads(model.to_json())
    compressed_config = compressConfig(config)
    return weight_base64, compressed_config

def get_text_from_file(file_path):
    # Read, then decode for py2 compat.
    text = open(file_path, 'rb').read().decode(encoding='utf-8')
    return text 

def get_text_from_dir(dir):

    text = ""
    file_paths = []

    def list_files_recursive(directory):
        for entry in os.listdir(directory):
            full_path = os.path.join(directory, entry)
            if os.path.isdir(full_path):
                list_files_recursive(full_path)
            elif os.path.isfile(full_path):
                file_paths.append(full_path)
    list_files_recursive(dir)

    for data_path in tqdm(file_paths):
        if data_path.endswith(".txt"):
            text += get_text_from_file(data_path)
            text += "\n"
        elif data_path.endswith(".pickle"):
            with open(data_path, 'rb') as f:
                data = pickle.load(f)
                text += data
                text += "\n"
        else:
            continue

    return text

def main():
    args = parse_args()
    # The embedding dimension
    config_path = args.config_path
    data_dir = args.data_dir
    output_dir = args.output_dir
    ckpt = args.checkpoint_path

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with open(config_path, "r") as f:
        config = json.load(f)
    
    resume_path = os.path.join(output_dir, "data.pickle")
    text = get_text_from_dir(data_dir)
    with open(resume_path, 'wb') as f:
        pickle.dump(text, f)
    text = list(text)

    X, y, vocab_to_index = create_dataset_from_text(text, config["seq_length"])
    vocabulary = list(vocab_to_index.keys())
    config["vocab_size"] = len(vocabulary)
    model = create_model(config, ckpt)


    checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(
        filepath=os.path.join(output_dir, "model.h5"),
        save_best_only=True,
        monitor="loss",
        mode="min",
        verbose = 1,
    )
        
    model.fit(X, y, batch_size = config["batch_size"], epochs=config["epoch_num"], callbacks=[checkpoint_callback])
    
    weight_base64, compressed_config = get_model_for_export(model)

    inscription = {
        "model_name": "RNN",
        "layers_config": compressed_config,
        "vocabulary": vocabulary,
        "weight_b64": weight_base64
    }
    inscription_json = json.dumps(inscription)
    write_to_file(os.path.join(output_dir, "model.json"), inscription_json)
    

if __name__ == "__main__":
    main()