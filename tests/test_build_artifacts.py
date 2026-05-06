import os
import glob
import zipfile
import pytest

def test_latest_build_artifact_contents():
    dist_dir = os.path.join(os.path.dirname(__file__), '..', 'dist')
    zip_files = glob.glob(os.path.join(dist_dir, 'TextCorrector_*_Windows.zip'))
    
    if not zip_files:
        pytest.skip("No TextCorrector zip files found in dist/")
        
    # Get the latest zip file by modification time
    latest_zip = max(zip_files, key=os.path.getmtime)
    
    expected_files = {
        'TextCorrector.exe',
        'config.json',
        'run.bat',
        'logo.png',
    }    
    with zipfile.ZipFile(latest_zip, 'r') as z:
        namelist = z.namelist()
        
        # The zip structure usually has a single root folder, e.g., 'TextCorrector_3.1.1_Windows/'
        # Let's find it.
        root_folders = set(name.split('/')[0] for name in namelist if '/' in name)
        main_root = None
        for root in root_folders:
            if root.startswith('TextCorrector_') and root.endswith('_Windows'):
                main_root = root
                break
                
        if not main_root:
            pytest.fail(f"Could not find the expected root folder in the zip file {latest_zip}. Roots: {root_folders}")
            
        # Get all items immediately under the root folder
        root_contents = set()
        for name in namelist:
            if name.startswith(main_root + '/'):
                rel_path = name[len(main_root) + 1:]
                if rel_path:
                    top_level = rel_path.split('/')[0]
                    root_contents.add(top_level)
                    
        missing_files = expected_files - root_contents
        assert not missing_files, f"Missing files in the zip artifact: {missing_files}"
        
        # Check for llama- folder
        has_llama_folder = any(item.startswith('llama-') for item in root_contents)
        assert has_llama_folder, "Missing 'llama-' folder in the zip artifact"
