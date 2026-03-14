import subprocess
import os


class PatchFullDiffExtractor:
    
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        
    def get_full_function_diff(self, hexsha: str) -> str:
        try:
            cmd = ['git', 'diff', '-W', f'{hexsha}^!']
            
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            
            return result.stdout
            
        except subprocess.CalledProcessError as e:
            print(f"Error executing git diff command: {e}")
            return ""
        except Exception as e:
            print(f"Unexpected error: {e}")
            return ""


def main():
    repo_path = "./linux"
    hexsha = "035b4989211dc1c8626e186d655ae8ca5141bb73"
    
    extractor = PatchFullDiffExtractor(repo_path)
    
    diff = extractor.get_full_function_diff(hexsha)
    print(diff)


if __name__ == "__main__":
    main()
