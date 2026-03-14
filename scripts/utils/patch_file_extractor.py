from pydriller import *
import os
from git import Repo
import sys
from icecream import ic
from pathlib import Path
from pydriller import Git 

class PatchFileExtractor:
    """A parser for extracting entire files before and after patch modifications
       This extracts complete files rather than individual functions.
    """    
    
    def __init__(self, repo, hexsha, output_dir='patch_files') -> None:
        self.hexsha = hexsha
        self.repo = repo
        self.output_dir = output_dir
        self.modified_files_info = []
        
        # Create output directories: output_dir/hexsha/before and output_dir/hexsha/after
        self.commit_dir = os.path.join(output_dir, hexsha)
        self.before_dir = os.path.join(self.commit_dir, 'before')
        self.after_dir = os.path.join(self.commit_dir, 'after')
        
        os.makedirs(self.before_dir, exist_ok=True)
        os.makedirs(self.after_dir, exist_ok=True)
        
    def get_commit_description(self):
        commit = self.repo.get_commit(self.hexsha)
        lines = commit.msg.splitlines()
        # Filter out certain tag prefixes if needed
        # For now, just return the full message
        return commit.msg.strip()
    
    def extract_modified_files(self):
        commit = self.repo.get_commit(self.hexsha)
        extracted_files = []
        
        try:
            for f in commit.modified_files:
                # Only process MODIFY operations (not ADD/DELETE)
                if f.change_type != ModificationType.MODIFY:
                    ic(f"Skipping {f.filename}: change type is {f.change_type}")
                    continue
                
                # Skip non-code files (you can customize this filter)
                if not self._is_code_file(f.filename):
                    ic(f"Skipping {f.filename}: not a code file")
                    continue
                
                file_info = self._extract_single_file(f)
                if file_info:
                    extracted_files.append(file_info)
                    
        except Exception as e:
            ic(f"Error processing commit {self.hexsha}: {e}")
            
        self.modified_files_info = extracted_files
        return extracted_files
    
    def _is_code_file(self, filename):
        """Check if file is a code file based on extension"""
        code_extensions = {'.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', 
                          '.py', '.java', '.js', '.ts', '.go', '.rs',
                          '.php', '.rb', '.scala', '.kt', '.swift'}
        
        file_ext = Path(filename).suffix.lower()
        return file_ext in code_extensions
    
    def _extract_single_file(self, modified_file):
        try:
            # Generate safe filename (replace path separators with underscores)
            safe_filename = modified_file.filename.replace('/', '_').replace('\\', '_')
            
            # Create file paths (without hexsha prefix since files are in hexsha folder)
            before_path = os.path.join(self.before_dir, safe_filename)
            after_path = os.path.join(self.after_dir, safe_filename)
            
            if modified_file.source_code_before is not None:
                with open(before_path, 'w', encoding='utf-8') as f:
                    f.write(modified_file.source_code_before)
            else:
                ic(f"No source_code_before for {modified_file.filename}")
                return None
            
            if modified_file.source_code is not None:
                with open(after_path, 'w', encoding='utf-8') as f:
                    f.write(modified_file.source_code)
            else:
                ic(f"No source_code for {modified_file.filename}")
                return None
            
            file_info = {
                'original_filename': modified_file.filename,
                'before_path': before_path,
                'after_path': after_path,
                'change_type': modified_file.change_type,
                'added_lines': modified_file.added_lines,
                'deleted_lines': modified_file.deleted_lines,
                'nloc': modified_file.nloc
            }
            
            ic(f"Extracted {modified_file.filename} -> {safe_filename}")
            return file_info
            
        except Exception as e:
            ic(f"Error extracting {modified_file.filename}: {e}")
            return None
    
    def extract_specific_file(self, target_filename):
        commit = self.repo.get_commit(self.hexsha)
        
        try:
            for f in commit.modified_files:
                if f.filename == target_filename or f.filename.endswith(target_filename):
                    if f.change_type != ModificationType.MODIFY:
                        ic(f"File {target_filename} was not modified (change type: {f.change_type})")
                        return None
                    
                    return self._extract_single_file(f)
            
            ic(f"File {target_filename} not found in commit {self.hexsha}")
            return None
            
        except Exception as e:
            ic(f"Error extracting specific file {target_filename}: {e}")
            return None
    
    def get_summary(self):
        if not self.modified_files_info:
            self.extract_modified_files()
        
        summary = {
            'commit_hash': self.hexsha,
            'total_files': len(self.modified_files_info),
            'files': []
        }
        
        for file_info in self.modified_files_info:
            summary['files'].append({
                'filename': file_info['original_filename'],
                'added_lines': file_info['added_lines'],
                'deleted_lines': file_info['deleted_lines'],
                'nloc': file_info['nloc']
            })
        
        return summary
    
    def cleanup(self):
        import shutil
        if os.path.exists(self.commit_dir):
            shutil.rmtree(self.commit_dir)
            ic(f"Cleaned up {self.commit_dir}")
