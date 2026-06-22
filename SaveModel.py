from huggingface_hub import snapshot_download

base_model_dir = "models/specter2_base_model"
adapter_dir = "models/specter2_adapter"

snapshot_download(repo_id="allenai/specter2_base", local_dir=base_model_dir)
snapshot_download(repo_id="allenai/specter2", local_dir=adapter_dir)


