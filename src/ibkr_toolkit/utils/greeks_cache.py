"""Greeks data cache manager

Provides functionality to cache and retrieve option Greeks data
when market is closed or data is unavailable.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from ..utils.logger import setup_logger


class GreeksCache:
    """Cache manager for option Greeks data"""
    
    def __init__(self, cache_dir: Path):
        """Initialize cache manager
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "greeks_cache.json"
        self.logger = setup_logger("greeks_cache")
    
    def save_greeks(self, options: List) -> bool:
        """Save Greeks data to cache
        
        Args:
            options: List of Position objects with Greeks data
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "options": []
            }
            
            for opt in options:
                if opt.delta is not None:  # Only cache positions with Greeks
                    option_data = {
                        "symbol": opt.symbol,
                        "strike": opt.strike,
                        "expiry": opt.expiry,
                        "right": opt.right,
                        "local_symbol": opt.local_symbol,
                        "position": opt.position,
                        "market_price": opt.market_price,
                        "market_value": opt.market_value,
                        "delta": opt.delta,
                        "gamma": opt.gamma,
                        "theta": opt.theta,
                        "vega": opt.vega
                    }
                    cache_data["options"].append(option_data)
            
            if not cache_data["options"]:
                self.logger.warning("No Greeks data to cache")
                return False
            
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            self.logger.info(f"Cached Greeks for {len(cache_data['options'])} options")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save Greeks cache: {e}", exc_info=True)
            return False
    
    def load_greeks(self, options: List, max_age_hours: int = 48) -> bool:
        """Load Greeks data from cache
        
        Args:
            options: List of Position objects to update with cached Greeks
            max_age_hours: Maximum age of cache in hours (default: 48)
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            if not self.cache_file.exists():
                self.logger.warning("No Greeks cache file found")
                return False
            
            with open(self.cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Check cache age
            cache_time = datetime.fromisoformat(cache_data["timestamp"])
            age = datetime.now() - cache_time
            
            if age > timedelta(hours=max_age_hours):
                self.logger.warning(
                    f"Greeks cache is too old ({age.total_seconds() / 3600:.1f} hours), "
                    f"max age is {max_age_hours} hours"
                )
                return False
            
            # Create lookup dict for fast matching
            cache_lookup = {}
            for cached_opt in cache_data["options"]:
                key = self._make_option_key(
                    cached_opt["symbol"],
                    cached_opt["strike"],
                    cached_opt["expiry"],
                    cached_opt["right"]
                )
                cache_lookup[key] = cached_opt
            
            # Update positions with cached Greeks
            updated_count = 0
            for opt in options:
                key = self._make_option_key(
                    opt.symbol,
                    opt.strike,
                    opt.expiry,
                    opt.right
                )
                
                if key in cache_lookup:
                    cached = cache_lookup[key]
                    opt.delta = cached["delta"]
                    opt.gamma = cached["gamma"]
                    opt.theta = cached["theta"]
                    opt.vega = cached["vega"]
                    updated_count += 1
            
            if updated_count > 0:
                self.logger.info(
                    f"Loaded cached Greeks for {updated_count}/{len(options)} options "
                    f"(cache age: {age.total_seconds() / 3600:.1f} hours)"
                )
                return True
            else:
                self.logger.warning("No matching options found in cache")
                return False
            
        except Exception as e:
            self.logger.error(f"Failed to load Greeks cache: {e}", exc_info=True)
            return False
    
    def _make_option_key(self, symbol: str, strike: float, expiry: str, right: str) -> str:
        """Create unique key for option identification
        
        Args:
            symbol: Option symbol
            strike: Strike price
            expiry: Expiration date
            right: Call or Put (C/P)
            
        Returns:
            Unique key string
        """
        return f"{symbol}_{strike}_{expiry}_{right}"
    
    def get_cache_info(self) -> Optional[Dict]:
        """Get cache file information
        
        Returns:
            Dict with cache info or None if cache doesn't exist
        """
        try:
            if not self.cache_file.exists():
                return None
            
            with open(self.cache_file, 'r') as f:
                cache_data = json.load(f)
            
            cache_time = datetime.fromisoformat(cache_data["timestamp"])
            age = datetime.now() - cache_time
            
            return {
                "timestamp": cache_time,
                "age_hours": age.total_seconds() / 3600,
                "option_count": len(cache_data["options"]),
                "file_path": str(self.cache_file)
            }
            
        except Exception as e:
            self.logger.error(f"Failed to get cache info: {e}")
            return None

