"""Notion Integration Service

This service handles syncing portfolio data to Notion databases.
Supports automatic database structure initialization and data synchronization.
"""

from datetime import datetime
from typing import Optional
from notion_client import Client
from notion_client.errors import APIResponseError

from ..models.position import Position, PositionSummary
from ..utils.logger import setup_logger


class NotionService:
    """Notion integration service for portfolio data synchronization"""

    def __init__(self, api_key: str, database_id: str):
        """Initialize Notion service

        Args:
            api_key: Notion integration API key
            database_id: Target database ID for syncing positions
        """
        self.client = Client(auth=api_key)
        self.database_id = database_id
        self.logger = setup_logger("notion_service")
        self._title_field_name = None  # Cache for title field name
        self._resolve_database_id()  # Resolve if it's a linked database view

    def _resolve_database_id(self):
        """Resolve the actual database ID if the provided ID is a linked database view"""
        try:
            db = self.client.databases.retrieve(database_id=self.database_id)

            # Check if this is a linked database (has data_sources)
            data_sources = db.get('data_sources', [])
            if data_sources and len(data_sources) > 0:
                source_id = data_sources[0]['id']
                source_name = data_sources[0].get('name', 'Unknown')

                self.logger.info(f"📌 Detected Linked Database View")
                self.logger.info(
                    f"   View: {db.get('title', [{}])[0].get('plain_text', 'Unnamed')}")
                self.logger.info(f"   Source: {source_name} ({source_id})")

                # Try to access the source database
                try:
                    source_db = self.client.databases.retrieve(
                        database_id=source_id)
                    self.logger.info(
                        f"✓ Source database is accessible, using it instead")
                    self.database_id = source_id  # Use source database ID
                except Exception:
                    self.logger.warning(
                        f"⚠️  Source database is not shared with integration")
                    self.logger.warning(
                        f"   Please share '{source_name}' with your integration")
                    self.logger.warning(
                        f"   Or use this database ID in .env: {source_id}")
                    # Keep using the view ID, but operations will be limited

        except Exception as e:
            self.logger.debug(f"Could not resolve database ID: {e}")

    def ensure_database_structure(self) -> bool:
        """Ensure database has all required properties

        This method checks and updates the database schema if needed.

        Returns:
            True if structure is valid or successfully updated
        """
        try:
            # Get current database schema
            database = self.client.databases.retrieve(
                database_id=self.database_id)
            current_props = database.get('properties', {})

            self.logger.debug(
                f"Database properties: {list(current_props.keys())}")

            # Find and cache the title field name
            for prop_name, prop_config in current_props.items():
                self.logger.debug(
                    f"Property '{prop_name}' has type: {prop_config.get('type')}")
                if prop_config.get('type') == 'title':
                    self._title_field_name = prop_name
                    self.logger.info(f"✓ Found title field: {prop_name}")
                    break

            # If no title field exists, we'll create one called "Position ID"
            if not self._title_field_name:
                self.logger.warning(
                    "No title field found, will create 'Position ID' as title")
                self._title_field_name = "Position ID"

            # Define required properties
            required_props = self._get_required_properties()

            # Add title field if needed
            if not current_props or self._title_field_name not in current_props:
                required_props[self._title_field_name] = {"title": {}}

            # Check if all required properties exist
            missing_props = {}
            for prop_name, prop_config in required_props.items():
                if prop_name not in current_props:
                    missing_props[prop_name] = prop_config
                    self.logger.info(f"Will add: {prop_name}")

            # Update database if there are missing properties
            if missing_props:
                self.logger.info(f"Adding {len(missing_props)} properties...")

                try:
                    self.client.databases.update(
                        database_id=self.database_id,
                        properties=missing_props
                    )
                    self.logger.info(
                        "✓ Database structure updated successfully")
                except APIResponseError as e:
                    self.logger.error(f"Failed to update database: {e}")
                    self.logger.error(
                        "This might be a Linked Database View - cannot modify structure")
                    return False
            else:
                self.logger.info("✓ Database structure is up to date")

            return True

        except APIResponseError as e:
            self.logger.error(f"Failed to ensure database structure: {e}")
            return False

    def _get_required_properties(self) -> dict:
        """Get required database properties definition

        Returns:
            Dictionary of property definitions
        """
        return {
            # Note: We don't redefine the Title field since database already has one
            # The existing title field (usually "Name") will be used for Position ID

            # Snapshot info
            "Snapshot Time": {
                "date": {}
            },

            # Basic info
            "Symbol": {
                "rich_text": {}
            },
            "Type": {
                "select": {
                    "options": [
                        {"name": "STK", "color": "blue"},
                        {"name": "OPT", "color": "purple"},
                        {"name": "FUT", "color": "orange"},
                        {"name": "CASH", "color": "gray"}
                    ]
                }
            },
            "Exchange": {
                "select": {
                    "options": [
                        {"name": "NASDAQ", "color": "blue"},
                        {"name": "NYSE", "color": "green"},
                        {"name": "AMEX", "color": "purple"},
                        {"name": "ARCA", "color": "yellow"},
                        {"name": "SMART", "color": "default"}
                    ]
                }
            },
            "Currency": {
                "select": {
                    "options": [
                        {"name": "USD", "color": "green"},
                        {"name": "EUR", "color": "blue"},
                        {"name": "GBP", "color": "yellow"},
                        {"name": "JPY", "color": "red"}
                    ]
                }
            },

            # Position details
            "Position Direction": {
                "select": {
                    "options": [
                        {"name": "Long", "color": "green"},
                        {"name": "Short", "color": "red"}
                    ]
                }
            },
            "Quantity": {
                "number": {
                    "format": "number"
                }
            },
            "Abs Quantity": {
                "number": {
                    "format": "number"
                }
            },

            # Price and value
            "Average Cost": {
                "number": {
                    "format": "dollar"
                }
            },
            "Market Price": {
                "number": {
                    "format": "dollar"
                }
            },
            "Market Value": {
                "number": {
                    "format": "dollar"
                }
            },

            # P&L
            "Unrealized P&L": {
                "number": {
                    "format": "dollar"
                }
            },
            "Realized P&L": {
                "number": {
                    "format": "dollar"
                }
            },
            "P&L Percentage": {
                "number": {
                    "format": "percent"
                }
            },

            # Additional info
            "Account": {
                "rich_text": {}
            },
            "Local Symbol": {
                "rich_text": {}
            },

            # Metadata
            "Last Updated": {
                "date": {}
            }
        }

    def sync_summary(self, summary: PositionSummary) -> dict:
        """Sync portfolio summary to Notion

        Creates a new snapshot entry for each position in the summary.

        Args:
            summary: PositionSummary object containing portfolio data

        Returns:
            Dictionary with sync results
        """
        self.logger.info(
            f"Starting sync for {summary.total_positions} positions...")

        results = {
            'success': 0,
            'failed': 0,
            'errors': []
        }

        snapshot_time = summary.update_time

        # Sync each position
        for position in summary.positions:
            try:
                self._create_position_page(position, snapshot_time)
                results['success'] += 1
            except Exception as e:
                results['failed'] += 1
                results['errors'].append({
                    'position': position.symbol,
                    'error': str(e)
                })
                self.logger.error(f"Failed to sync {position.symbol}: {e}")

        self.logger.info(
            f"Sync completed: {results['success']} success, {results['failed']} failed"
        )

        return results

    def _create_position_page(self, position: Position, snapshot_time: datetime):
        """Create a new position page in Notion

        Args:
            position: Position object
            snapshot_time: Snapshot timestamp
        """
        # Generate unique position ID
        position_id = self._generate_position_id(position, snapshot_time)

        # Determine position direction
        direction = "Long" if position.position >= 0 else "Short"
        abs_quantity = abs(position.position)

        # Build page properties
        # Use the database's existing title field (cached from ensure_database_structure)
        properties = {
            self._title_field_name: {
                "title": [
                    {
                        "text": {
                            "content": position_id
                        }
                    }
                ]
            },
            "Snapshot Time": {
                "date": {
                    "start": snapshot_time.isoformat()
                }
            },
            "Symbol": {
                "rich_text": [
                    {
                        "text": {
                            "content": position.symbol
                        }
                    }
                ]
            },
            "Type": {
                "select": {
                    "name": position.contract_type
                }
            },
            "Exchange": {
                "select": {
                    "name": position.exchange
                }
            },
            "Currency": {
                "select": {
                    "name": position.currency
                }
            },
            "Position Direction": {
                "select": {
                    "name": direction
                }
            },
            "Quantity": {
                "number": position.position
            },
            "Abs Quantity": {
                "number": abs_quantity
            },
            "Average Cost": {
                "number": position.avg_cost
            },
            "Market Price": {
                "number": position.market_price
            },
            "Market Value": {
                "number": position.market_value
            },
            "Unrealized P&L": {
                "number": position.unrealized_pnl
            },
            "Realized P&L": {
                "number": position.realized_pnl
            },
            "P&L Percentage": {
                "number": position.pnl_percent / 100  # Convert to decimal for percent format
            },
            "Last Updated": {
                "date": {
                    "start": position.update_time.isoformat()
                }
            }
        }

        # Add optional fields
        if position.account:
            properties["Account"] = {
                "rich_text": [
                    {
                        "text": {
                            "content": position.account
                        }
                    }
                ]
            }

        if position.local_symbol:
            properties["Local Symbol"] = {
                "rich_text": [
                    {
                        "text": {
                            "content": position.local_symbol
                        }
                    }
                ]
            }

        # Create the page
        self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=properties
        )

        self.logger.debug(f"Created page for {position.symbol}")

    def _generate_position_id(self, position: Position, snapshot_time: datetime) -> str:
        """Generate unique position ID

        Args:
            position: Position object
            snapshot_time: Snapshot timestamp

        Returns:
            Unique position identifier
        """
        timestamp_str = snapshot_time.strftime("%Y%m%d_%H%M%S")
        account_str = position.account or "default"

        # Format: SYMBOL_TYPE_ACCOUNT_TIMESTAMP
        position_id = f"{position.symbol}_{position.contract_type}_{account_str}_{timestamp_str}"

        return position_id

    def test_connection(self) -> bool:
        """Test connection to Notion API and database access

        Returns:
            True if connection is successful
        """
        try:
            database = self.client.databases.retrieve(
                database_id=self.database_id)
            title = database.get('title', [{}])[0].get('plain_text', 'Unknown')
            self.logger.info(f"✓ Successfully connected to database: {title}")

            # Check if database has properties
            props = database.get('properties', {})
            if not props:
                self.logger.warning("⚠️  Database has no properties yet")
                self.logger.info(
                    "Will try to add properties during initialization...")
                # Don't fail - we'll try to add properties later
            else:
                self.logger.info(f"✓ Database has {len(props)} properties")

            return True
        except APIResponseError as e:
            self.logger.error(f"❌ Connection test failed: {e}")
            return False
