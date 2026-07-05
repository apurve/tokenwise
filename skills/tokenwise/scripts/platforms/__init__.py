from .base import PlatformProvider
from .claude import ClaudePlatform
from .antigravity import AntigravityPlatform

def get_platform(name: str = None) -> PlatformProvider:
    if name == "antigravity":
        return AntigravityPlatform()
    if name == "claude":
        return ClaudePlatform()
    
    # Auto-detect
    import os
    from pathlib import Path
    
    # If in an antigravity workspace or home has .gemini
    if Path.home().joinpath(".gemini", "antigravity-ide").exists() and not Path.home().joinpath(".claude").exists():
        return AntigravityPlatform()
    
    return ClaudePlatform()
