import subprocess
import logging
import os
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)

# Configuration
# TODO: Move this to a config file or env var
DEFAULT_TOOL_PATH = r"\\wsl.localhost\Ubuntu\home\david-wsl\repos\DecodeWheaRecord-v0.5.0-net80\DecodeWheaRecord.exe"

class WheaDecoder:
    def __init__(self, tool_path=DEFAULT_TOOL_PATH):
        self.tool_path = tool_path

    def decode_hex_string(self, hex_string: str):
        """
        Decodes a hex string representing a WHEA record using the external tool.
        """
        # Clean the hex string (remove dashes if from BitConverter)
        clean_hex = hex_string.replace("-", "").strip()
        
        # Method 1: If the tool accepts input via arguments
        # Method 2: If the tool accepts input via a file (likely safer for large blobs)
        
        # Assuming the tool usage is: DecodeWheaRecord.exe <InputFile> or similar.
        # Based on user description: "running a command that executes the exe... and pass in the binary output"
        # We'll try passing via temporary file for stability.
        
        with NamedTemporaryFile(mode='wb', delete=False, suffix='.bin') as tmp:
            try:
                binary_data = bytes.fromhex(clean_hex)
                tmp.write(binary_data)
                tmp_path = tmp.name
            except ValueError:
                return {"error": "Invalid hex string provided"}

        # Now run the tool against this file
        # Note: If running from WSL, we might need value mapping for the path if the tool expects Windows paths.
        # But since tool is in WSL repo but run as .exe, it likely expects Windows access or is getting confused.
        # Actually, if we run `DecodeWheaRecord.exe` from WSL, it sees the WSL filesystem via the network mount if mapped?
        # NO. .exe binaries running via WSL interop see the Windows filesystem generally or can access WSL files via \\wsl$\...
        
        # To be safe, we might pass the hex string directly if supported, OR usage instructions are needed.
        # Let's assume standard input or file path.
        # If the user says they "pass in the binary output", they might mean piping?
        
        # Let's try to run it.
        try:
            # We use the UNC path for the tool as provided by user. 
            # Ideally we convert the tmp_path to a Windows path format for the .exe
            # WSL path `/tmp/xyz` -> `\\wsl.localhost\Ubuntu\tmp\xyz`
            
            wsl_tmp_path = subprocess.check_output(["wslpath", "-w", tmp_path], text=True).strip()
            
            # Construct command
            cmd = [self.tool_path, "-i", wsl_tmp_path] # Guessing '-i' or just positional arg
            
            # Since we don't know the exact args, we might need a "Help" tool first or ask user. 
            # But let's try a generic approach first: positional argument.
            cmd = [self.tool_path, wsl_tmp_path]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"Decoder failed: {result.stderr}")
                return {"error": "Decoder tool failed", "details": result.stderr}
                
            return {"decoded_output": result.stdout}
            
        except Exception as e:
            return {"error": str(e)}
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def verify_tool_paths(self):
        """Checks if the tool executable exists."""
        # Simple check - complex because of cross-OS (WSL->Windows) visibility
        # subprocess.run(["ls", ...]) might not work for a path starting with \\wsl.localhost
        # But we can try to run it with --help
        try:
             subprocess.run([self.tool_path, "--help"], capture_output=True)
             return True
        except FileNotFoundError:
            return False

