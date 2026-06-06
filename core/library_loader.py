"""
Library Loader for the Universal Social Assistant

Manages dynamic libraries and provides fallback mechanisms
"""
import os
import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
from core.test_runner import TestRunner

logger = logging.getLogger(__name__)

class LibraryLoader:
    """Loader for managing libraries and their dependencies"""
    
    def __init__(self, registry_path: str = "./libraries/registry.json", 
                 core_libraries_path: str = "./core_libraries"):
        self.registry_path = Path(registry_path)
        self.core_libraries_path = Path(core_libraries_path)
        self.registry = {}
        self.loaded_libraries = {}
        self.test_runner = TestRunner()
        self._load_registry()
    
    def _load_registry(self):
        """Load the library registry"""
        try:
            if self.registry_path.exists():
                with open(self.registry_path, 'r', encoding='utf-8') as f:
                    self.registry = json.load(f)
            else:
                logger.warning(f"Library registry not found at {self.registry_path}")
                self.registry = {}
        except Exception as e:
            logger.error(f"Error loading library registry: {e}")
            self.registry = {}
    
    def save_registry(self):
        """Save the current library registry to file"""
        try:
            with open(self.registry_path, 'w', encoding='utf-8') as f:
                json.dump(self.registry, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving library registry: {e}")
    
    def is_library_module(self, module_name: str) -> bool:
        """Check if a module name refers to a library module"""
        # Check in the registry if the module has a type field "library"
        if module_name in self.registry:
            return self.registry[module_name].get("type", "module") == "library"
        return False
    
    def is_library_enabled(self, library_name: str) -> bool:
        """Check if a library is enabled"""
        if library_name in self.registry:
            return self.registry[library_name].get("enabled", False)
        return False
    
    async def load_enabled_libraries(self) -> Dict[str, bool]:
        """Load all enabled libraries from registry"""
        results = {}
        
        for lib_name, lib_info in self.registry.items():
            if lib_info.get("enabled", False):
                result = await self.load_library(lib_name)
                results[lib_name] = result
        
        return results
    
    async def load_library(self, library_name: str) -> bool:
        """Load a specific library"""
        try:
            if not library_name in self.registry:
                logger.warning(f"Library {library_name} not found in registry")
                return False
            
            lib_info = self.registry[library_name]
            lib_path = Path(lib_info["path"])
            
            # Check if library path exists
            if not lib_path.exists():
                logger.error(f"Library path {lib_path} does not exist")
                return False
            
            # Run library tests
            test_result = await self.test_runner.run_library_tests(lib_path)
            if not test_result:
                # Mark as broken and fall back if necessary
                self._mark_library_broken(library_name)
                logger.warning(f"Library {library_name} tests failed")
                return False
            
            # If we get here, library is healthy
            self._mark_library_healthy(library_name)
            
            # Store library info (in a real implementation, we'd load the actual module)
            logger.info(f"Library {library_name} loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error loading library {library_name}: {e}")
            self._mark_library_broken(library_name)
            return False
    
    def _mark_library_healthy(self, library_name: str):
        """Mark library as healthy in registry"""
        if library_name in self.registry:
            self.registry[library_name]["status"] = "healthy"
            self.save_registry()
    
    def _mark_library_broken(self, library_name: str):
        """Mark library as broken in registry"""
        if library_name in self.registry:
            self.registry[library_name]["status"] = "broken"
            self.save_registry()
    
    def get_fallback_library(self, library_name: str) -> Optional[str]:
        """Get fallback library for a broken library if one exists"""
        if library_name in self.registry:
            lib_info = self.registry[library_name]
            return lib_info.get("fallback")
        return None
    
    def get_library_status(self, library_name: str) -> str:
        """Get the status of a library"""
        if library_name in self.registry:
            return self.registry[library_name].get("status", "unknown")
        return "unknown"
    
    def get_library_path(self, library_name: str) -> Optional[str]:
        """Get the module path for a library"""
        if library_name in self.registry:
            return self.registry[library_name].get("path")
        return None
    
    async def health_check_all_libraries(self):
        """Run health check on all libraries"""
        for lib_name in self.registry.keys():
            if self.registry[lib_name].get("enabled", False):
                await self.load_library(lib_name)
    
    async def health_check_library(self, library_name: str):
        """Run health check on a specific library"""
        if self.registry[library_name].get("enabled", False):
            await self.load_library(library_name)