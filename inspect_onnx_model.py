"""
Diagnostic script to inspect ONNX decoder model inputs.
This follows the systematic debugging framework's requirement to gather evidence
before attempting any fixes.
"""
import onnxruntime as ort
import os

model_dir = "onnx_models/grammar_t5"
decoder_path = os.path.join(model_dir, "decoder_model.onnx")

print("=" * 60)
print("ONNX DECODER MODEL INSPECTION")
print("=" * 60)

if not os.path.exists(decoder_path):
    print(f"ERROR: Decoder model not found at {decoder_path}")
    exit(1)

try:
    session = ort.InferenceSession(decoder_path, providers=['CPUExecutionProvider'])
    
    print("\n📋 MODEL INPUTS:")
    print("-" * 60)
    for inp in session.get_inputs():
        print(f"  Name: {inp.name}")
        print(f"  Shape: {inp.shape}")
        print(f"  Type: {inp.type}")
        print()
    
    print("\n📋 MODEL OUTPUTS:")
    print("-" * 60)
    for out in session.get_outputs():
        print(f"  Name: {out.name}")
        print(f"  Shape: {out.shape}")
        print(f"  Type: {out.type}")
        print()
    
    print("\n📋 PROVIDERS:")
    print(f"  {session.get_providers()}")
    
    # Count past_key_values inputs
    past_kv_inputs = [inp for inp in session.get_inputs() if 'past_key_values' in inp.name]
    print(f"\n📋 PAST_KEY_VALUES INPUTS COUNT: {len(past_kv_inputs)}")
    
    # Group by layer
    layers = set()
    for inp in past_kv_inputs:
        parts = inp.name.split('.')
        if len(parts) >= 2:
            layers.add(parts[1])
    print(f"  Layers: {sorted(layers)}")
    
    # Show first few past_key_values inputs
    print("\n📋 FIRST 5 PAST_KEY_VALUES INPUTS:")
    for inp in past_kv_inputs[:5]:
        print(f"  {inp.name}: shape={inp.shape}, type={inp.type}")
    
    print("\n" + "=" * 60)
    print("INSPECTION COMPLETE")
    print("=" * 60)
    
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()