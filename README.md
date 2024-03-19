# rnn-training
## Config file

Config file format
```json
{
  "embedding_dim": 32,
  "rnn_units": 128,
  "batch_size": 128,
  "epoch_num": 10,
  "seq_length": 30
}
```
- ``embedding_dim``: should be in range [32, 512].
- ``rnn_units``: should be in range [32, 512].
- ``epoch_num``: Optional (should be maximum 100).
- ``seq_length``: Optional (should be in range [20, 30]).

## Running command

```
conda create -n env_name python=3.9
pip install -r requirements.txt
python rnn_training.py --config-path CONFIG_PATH --data-path DATASET_PATH --output-dir OUTPUT_DIR

EXAMPLE:
python rnn_training.py --data-dir ./test_training_data --config-path ./config.json
```
## Dataset
11 datasets could be downloaded on this link [rnn-char-dataset](https://drive.google.com/drive/folders/1XZlfFifDSJsf02Oy-JjsGxMkPPt15NNw?usp=sharing).