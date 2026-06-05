"""
Fallback System for Universal Social Assistant
"""
import logging
from typing import Callable, Any, Optional
from functools import wraps
import asyncio
from core.library_loader import LibraryLoader

logger = logging.getLogger(__name__)

class FallbackSystem:
    """
    Fallback system for handling external component failures gracefully
    """
    
    def __init__(self):
        """Initialize fallback system"""
        self.fallback_enabled = True
        self.library_loader = LibraryLoader()
    
    async def with_fallback(self, component_name: str, fn: Callable, *args, **kwargs) -> Any:
        """
        Execute function with fallback handling
        
        Args:
            component_name: Name of the component being called
            fn: Function to execute
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of function or fallback data
        """
        try:
            # Execute the function
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            
            logger.debug(f"Successfully executed {component_name}")
            return result
            
        except Exception as e:
            logger.warning(f"Component {component_name} failed: {e}")
            
            # Return appropriate fallback data based on component type
            fallback_data = self._get_fallback_data(component_name, e)
            
            if fallback_data is not None:
                logger.info(f"Returning fallback for {component_name}")
                return fallback_data
            else:
                # If no fallback defined, re-raise the exception
                logger.error(f"No fallback defined for {component_name}")
                raise
    
    def _get_fallback_data(self, component_name: str, error: Exception) -> Any:
        """
        Get fallback data for a specific component
        
        Args:
            component_name: Name of the component
            error: The exception that occurred
            
        Returns:
            Fallback data or None if no fallback defined
        """
        # Special handling for libraries using fallback system
        if component_name in self.library_loader.registry:
            # Check if this is a library that's disabled or broken
            if self.library_loader.get_library_status(component_name) in ("broken", "disabled"):
                # Try to get fallback for this library
                fallback_lib = self.library_loader.get_fallback_library(component_name)
                if fallback_lib:
                    logger.info(f"Using fallback library {fallback_lib} for {component_name}")
                    # Return placeholder fallback that indicates library replacement
                    return {"error": f"Library {component_name} unavailable, using fallback", "fallback_used": True}
        
        fallback_map = {
            "mem0": {
                "type": "dict",
                "data": {}
            },
            "database": {
                "type": "dict", 
                "data": {"error": "Database unavailable", "fallback": True}
            },
            "psychology": {
                "type": "dict",
                "data": {"error": "Psychology unavailable", "fallback": True}
            },
            "digital_twin": {
                "type": "dict",
                "data": {"error": "Digital twin unavailable", "fallback": True}
            },
            "user_system": {
                "type": "dict",
                "data": {"error": "User system unavailable", "fallback": True}
            },
            "group_behavior": {
                "type": "dict",
                "data": {"error": "Group behavior unavailable", "fallback": True}
            },
            "persona_engine": {
                "type": "dict",
                "data": {"error": "Persona engine unavailable", "fallback": True}
            }
        }
        
        if component_name in fallback_map:
            fallback_config = fallback_map[component_name]
            return fallback_config["data"]
        
        # Default fallback for unknown components (just return None to indicate error)
        return None

# Global fallback system instance
fallback_system = FallbackSystem()