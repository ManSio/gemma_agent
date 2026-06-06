"""
Module Loader for the Universal Social Assistant

Handles automatic detection, loading, and management of modules
"""
import os
import json
import logging
import asyncio
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
from core.library_loader import LibraryLoader
from core.test_runner import TestRunner

logger = logging.getLogger(__name__)

class ModuleLoader:
    """Loader for all modules including dynamic libraries and core libraries"""
    
    def __init__(self, modules_path: str = "./modules", library_loader: LibraryLoader = None):
        self.modules_path = Path(modules_path)
        self.library_loader = library_loader
        self.plugin_registry = None
        self.test_runner = TestRunner()
        self.loaded_modules: Dict[str, Any] = {}
        
        # Initialize library loader if not provided
        if not self.library_loader:
            self.library_loader = LibraryLoader()
    
    async def load_all_modules(self) -> Dict[str, bool]:
        """Load all modules from modules directory"""
        if not self.modules_path.exists():
            logger.warning(f"Modules directory {self.modules_path} does not exist")
            return {}
        
        results = {}
        
        # Lazy import PluginRegistry to avoid circular dependency
        from core.plugin_registry import PluginRegistry
        self.plugin_registry = PluginRegistry(str(self.modules_path))
        
        # First, load and validate all modules
        for module_dir in self.modules_path.iterdir():
            if module_dir.is_dir():
                module_name = module_dir.name
                
                # Check if this is a library module that should be loaded via library loader
                if self.library_loader.is_library_module(module_name):
                    logger.debug(f"Module {module_name} is a library module, skip from direct loading")
                    continue
                
                # Load module
                result = await self.load_module(module_dir)
                results[module_name] = result
        
        return results
    
    async def load_module(self, module_path: Path) -> bool:
        """Load a single module"""
        try:
            # Check if module exists
            manifest_path = module_path / "module.json"
            if not manifest_path.exists():
                logger.warning(f"Module manifest not found in {module_path}")
                return False
                
            # Read manifest
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest_data = json.load(f)
            
            # Validate manifest
            if not self._validate_manifest(manifest_data):
                logger.error(f"Invalid module manifest for {module_path}")
                return False
            
            module_name = manifest_data.get("name")
            if not module_name:
                logger.error(f"Module name not specified in {manifest_path}")
                return False
            
            # Check module dependencies
            requires = manifest_data.get("requires", [])
            for required in requires:
                if not self.library_loader.is_library_enabled(required):
                    logger.warning(f"Module {module_name} requires library {required} which is not enabled, skipping")
                    return False
            
            # Run tests before loading
            test_result = await self.test_runner.run_module_tests(module_path)
            if not test_result:
                logger.warning(f"Module {module_name} tests failed, skipping load")
                return False
            
            # Load module
            module_instance = self.plugin_registry.load_module(module_path)
            if not module_instance:
                logger.error(f"Failed to load module {module_name}")
                return False
            
            # Try to enable module
            if self.plugin_registry.enable_module(module_name):
                self.loaded_modules[module_name] = module_instance
                logger.info(f"Module {module_name} loaded and enabled successfully")
                return True
            else:
                logger.error(f"Failed to enable module {module_name}")
                return False
                
        except Exception as e:
            logger.error(f"Error loading module from {module_path}: {e}")
            return False
    
    def get_module(self, name: str) -> Optional[Any]:
        """Get loaded module by name"""
        return self.loaded_modules.get(name)
    
    def get_loaded_modules(self) -> List[str]:
        """Get list of loaded module names"""
        return list(self.loaded_modules.keys())
    
    def _validate_manifest(self, manifest_data: Dict[str, Any]) -> bool:
        """Validate module manifest"""
        required_fields = ["name", "version", "type"]
        
        for field in required_fields:
            if field not in manifest_data:
                logger.error(f"Required field '{field}' missing in manifest")
                return False
        
        # Validate entrypoint format if provided
        if "entrypoint" in manifest_data:
            entrypoint = manifest_data["entrypoint"]
            if not isinstance(entrypoint, str) or ":" not in entrypoint:
                logger.error(f"Invalid entrypoint format: {entrypoint}")
                return False
        
        # Validate commands if provided
        if "commands" in manifest_data:
            if not isinstance(manifest_data["commands"], list):
                logger.error(f"Commands must be a list")
                return False
        
        # Validate events if provided
        if "events" in manifest_data:
            if not isinstance(manifest_data["events"], list):
                logger.error(f"Events must be a list")
                return False
        
        # Validate requires if provided
        if "requires" in manifest_data:
            if not isinstance(manifest_data["requires"], list):
                logger.error(f"Requires must be a list")
                return False
        
        return True