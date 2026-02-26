import os
import shutil
from pathlib import Path

root = Path(r"d:\Updated POC - Copy\Updated POC\Parsehub_project")
docs = root / "docs"
scripts = root / "scripts"

docs.mkdir(exist_ok=True)
scripts.mkdir(exist_ok=True)

keep = {"backend", "frontend", "docs", "scripts", ".git", ".gitignore", "README.md", "vercel.json", "final_cleanup.py", ".env"}

for item in root.iterdir():
    if item.name in keep:
        continue
    
    try:
        dest_dir = docs if item.suffix in [".md", ".txt"] else scripts
        dest_path = dest_dir / item.name
        
        if item.is_file():
            if dest_path.exists():
                os.remove(dest_path)
            shutil.move(str(item), str(dest_path))
        elif item.is_dir():
            if item.name == "__pycache__":
                shutil.rmtree(item)
            else:
                # If it's a random dir, move to docs
                if dest_path.exists():
                    shutil.rmtree(dest_path)
                shutil.move(str(item), str(dest_path))
    except Exception as e:
        print(f"Error moving {item.name}: {e}")

print("Root cleanup complete.")
