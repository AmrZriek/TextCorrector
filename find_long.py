import os
import glob
import time

brain_dir = 'C:/Users/Amrzr/.gemini/antigravity/brain'
folders = [f for f in glob.glob(f'{brain_dir}/*') if os.path.isdir(f)]

now = time.time()
recent = [(f, os.path.getmtime(f)) for f in folders if now - os.path.getmtime(f) < 86400 * 5]
recent.sort(key=lambda x: x[1], reverse=True)

with open('temp_check.txt', 'w', encoding='utf-8') as f_out:
    for f, m in recent:
        cid = os.path.basename(f)
        overview_path = os.path.join(f, '.system_generated', 'logs', 'overview.txt')
        lines = 0
        if os.path.exists(overview_path):
            with open(overview_path, 'r', encoding='utf-8', errors='ignore') as o:
                lines = len(o.readlines())
        f_out.write(f'{cid} modified: {time.ctime(m)} lines: {lines}\n')
