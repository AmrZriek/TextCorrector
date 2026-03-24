import os
from huggingface_hub import hf_hub_download

def download_model():
    model_id = "Xenova/gpt2"
    save_dir = "onnx_models/gpt2"
    os.makedirs(save_dir, exist_ok=True)
    
    print("Downloading tokenizer.json...")
    hf_hub_download(repo_id=model_id, filename="tokenizer.json", local_dir=save_dir)
    
    print("Downloading model.onnx...")
    try:
        # xenova models often keep onnx files in an onnx/ subdirectory
        path = hf_hub_download(repo_id=model_id, filename="onnx/decoder_model_merged.onnx")
        # copy to model.onnx
        import shutil
        shutil.copy(path, os.path.join(save_dir, "model.onnx"))
        print("Successfully downloaded and renamed decoder_model_merged.onnx to model.onnx")
    except Exception as e:
        print(f"Error downloading: {e}")
        try:
            # Maybe there's a simple model.onnx?
            path = hf_hub_download(repo_id=model_id, filename="onnx/model.onnx")
            import shutil
            shutil.copy(path, os.path.join(save_dir, "model.onnx"))
            print("Successfully downloaded and renamed model.onnx")
        except Exception as e2:
            print(f"Fallback Error: {e2}")

if __name__ == "__main__":
    download_model()
