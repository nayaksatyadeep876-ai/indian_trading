from logzero import logger
from SmartApi.smartConnect import SmartConnect
import pyotp
import os
import json
import requests
import http.client  # Added for comprehensive market data fetching
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
import pandas as pd

# Load environment variables from .env file
load_dotenv()

class AngelOneConnection:
    """Angel One SmartAPI connection using the working method"""
    
    def __init__(self):
        self.smart_api = None
        self.is_connected = False
        self.access_token = None
        self.refresh_token = None
        self.feed_token = None
        self.user_profile = None
        
    def initialize_smartapi(self):
        """Initialize SmartAPI connection with credentials from .env file"""
        try:
            # Get credentials from environment variables
            api_key = os.getenv('ANGEL_ONE_API_KEY')
            client_id = os.getenv('ANGEL_ONE_CLIENT_ID')
            password = os.getenv('ANGEL_ONE_PASSWORD')
            totp_secret = os.getenv('ANGEL_ONE_TOTP_SECRET')
            
            # Validate required credentials
            if not all([api_key, client_id, password, totp_secret]):
                logger.error("Missing required Angel One credentials in .env file")
                return False
            
            # Initialize SmartAPI
            self.smart_api = SmartConnect(api_key)
            
            # Generate TOTP
            totp = pyotp.TOTP(totp_secret).now()
            logger.info(f"Generated TOTP: {totp}")
            logger.info(f"TOTP Secret length: {len(totp_secret)}")
            logger.info(f"Current time: {datetime.now()}")
            
            # Test TOTP generation
            try:
                test_totp = pyotp.TOTP(totp_secret)
                logger.info(f"TOTP object created successfully")
                logger.info(f"TOTP interval: {test_totp.interval}")
            except Exception as e:
                logger.error(f"TOTP object creation failed: {e}")
            
            # Generate session
            data = self.smart_api.generateSession(
                client_id,
                password,
                totp
            )
            
            if data['status']:
                logger.info("Successfully connected to Angel One API")
                self.access_token = data['data']['jwtToken']
                self.refresh_token = data['data']['refreshToken']
                self.feed_token = data['data']['feedToken']
                self.is_connected = True
                
                # Get user profile
                self.user_profile = self.smart_api.getProfile(self.refresh_token)
                logger.info(f"Logged in as: {self.user_profile['data']['name']}")
                logger.info(f"Exchanges: {self.user_profile['data']['exchanges']}")
                
                return True
            else:
                logger.error(f"Connection failed: {data['message']}")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing SmartAPI: {e}")
            return False
    
    def get_profile(self):
        """Get user profile information"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.getProfile(self.refresh_token)
        except Exception as e:
            logger.error(f"Error getting profile: {e}")
            return None
    
    def get_funds(self):
        """Get available funds"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.rmsLimit()
        except Exception as e:
            logger.error(f"Error getting funds: {e}")
            return None
    
    def get_holdings(self):
        """Get current holdings"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.holding()
        except Exception as e:
            logger.error(f"Error getting holdings: {e}")
            return None
    
    def get_positions(self):
        """Get current positions"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.position()
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return None
    
    def get_historical_data(self, symbol, exchange, interval, from_date, to_date):
        """Get historical data for a symbol"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            # Convert dates to the format expected by Angel One API
            # Angel One expects dates in YYYY-MM-DD HH:MM format
            from_date_formatted = from_date.strftime('%Y-%m-%d %H:%M')
            to_date_formatted = to_date.strftime('%Y-%m-%d %H:%M')
            
            return self.smart_api.getCandleData({
                "exchange": exchange,
                "symboltoken": symbol,
                "interval": interval,
                "fromdate": from_date_formatted,
                "todate": to_date_formatted
            })
        except Exception as e:
            logger.error(f"Error getting historical data: {e}")
            return None
    
    def refresh_totp(self):
        """Refresh TOTP for new session if needed"""
        try:
            totp_secret = os.getenv('ANGEL_ONE_TOTP_SECRET')
            if not totp_secret:
                logger.error("TOTP secret not found in environment variables")
                return None
            
            totp = pyotp.TOTP(totp_secret).now()
            logger.info(f"Refreshed TOTP: {totp}")
            return totp
        except Exception as e:
            logger.error(f"Error refreshing TOTP: {e}")
            return None
    
    def reconnect_with_fresh_totp(self):
        """Reconnect to Angel One with a fresh TOTP"""
        try:
            logger.info("Attempting to reconnect with fresh TOTP...")
            self.is_connected = False
            self.access_token = None
            self.refresh_token = None
            self.feed_token = None
            self.user_profile = None
            
            # Try to reconnect
            return self.initialize_smartapi()
        except Exception as e:
            logger.error(f"Error reconnecting with fresh TOTP: {e}")
            return False

    def get_historical_data_wrapper(self, symbol, interval='1d', period='1d'):
        """Wrapper method for get_historical_data with simplified parameters"""
        if not self.is_connected or not self.smart_api:
            return None
        
        try:
            # Map symbol to exchange and symbol token
            symbol_map = {
                'NIFTY50': {'exchange': 'NSE', 'symboltoken': '26000'},
                'BANKNIFTY': {'exchange': 'NSE', 'symboltoken': '26009'},
                'SENSEX': {'exchange': 'BSE', 'symboltoken': '1'},
                'FINNIFTY': {'exchange': 'NSE', 'symboltoken': '26037'},
                'MIDCPNIFTY': {'exchange': 'NSE', 'symboltoken': '26017'},
                'RELIANCE': {'exchange': 'NSE', 'symboltoken': '2881'},
                'TCS': {'exchange': 'NSE', 'symboltoken': '2951'},
                'HDFCBANK': {'exchange': 'NSE', 'symboltoken': '1333'},
                'INFY': {'exchange': 'NSE', 'symboltoken': '408'},
                'ICICIBANK': {'exchange': 'NSE', 'symboltoken': '496'},
                'SBIN': {'exchange': 'NSE', 'symboltoken': '3045'},
                'BHARTIARTL': {'exchange': 'NSE', 'symboltoken': '319'},
                'KOTAKBANK': {'exchange': 'NSE', 'symboltoken': '1922'},
                'BAJFINANCE': {'exchange': 'NSE', 'symboltoken': '317'},
                'HINDUNILVR': {'exchange': 'NSE', 'symboltoken': '1330'}
            }
            
            if symbol not in symbol_map:
                logger.error(f"Symbol {symbol} not found in symbol mapping")
                return None
            
            symbol_info = symbol_map[symbol]
            exchange = symbol_info['exchange']
            symbol_token = symbol_info['symboltoken']
            
            # Calculate date range based on period
            end_date = datetime.now()
            if period == '1d':
                start_date = end_date - timedelta(days=1)
            elif period == '7d':
                start_date = end_date - timedelta(days=7)
            elif period == '1m':
                start_date = end_date - timedelta(days=30)
            elif period == '3m':
                start_date = end_date - timedelta(days=90)
            elif period == '6m':
                start_date = end_date - timedelta(days=180)
            elif period == '1y':
                start_date = end_date - timedelta(days=365)
            else:
                start_date = end_date - timedelta(days=1)
            
            # Map interval
            interval_map = {
                '1m': 'ONE_MINUTE',
                '5m': 'FIVE_MINUTE',
                '15m': 'FIFTEEN_MINUTE',
                '30m': 'THIRTY_MINUTE',
                '1h': 'ONE_HOUR',
                '1d': 'ONE_DAY'
            }
            
            api_interval = interval_map.get(interval, 'ONE_DAY')
            
            # Get data from Angel One API
            logger.info(f"Calling Angel One API for {symbol} with token {symbol_token}, exchange {exchange}, interval {api_interval}, from {start_date} to {end_date}")
            data = self.get_historical_data(symbol_token, exchange, api_interval, start_date, end_date)
            logger.info(f"Angel One API response: {data}")
            
            if not data or not data.get('data'):
                logger.warning(f"No data returned for {symbol}")
                return None
            
            # Convert to pandas DataFrame
            df_data = data['data']
            if not df_data:
                logger.warning(f"Empty data array for {symbol}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(df_data)
            
            # Rename columns to match expected format
            column_mapping = {
                0: 'Datetime',
                1: 'Open',
                2: 'High',
                3: 'Low',
                4: 'Close',
                5: 'Volume'
            }
            
            df = df.rename(columns=column_mapping)
            
            # Convert datetime column
            if 'Datetime' in df.columns:
                df['Datetime'] = pd.to_datetime(df['Datetime'])
                df.set_index('Datetime', inplace=True)
            
            # Convert numeric columns
            numeric_columns = ['Open', 'High', 'Low', 'Close', 'Volume']
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Remove any rows with NaN values
            df = df.dropna()
            
            logger.info(f"Successfully fetched {len(df)} records for {symbol}")
            return df
            
        except Exception as e:
            logger.error(f"Error in get_historical_data_wrapper for {symbol}: {e}")
            return None
    
    def place_order(self, order_params):
        """Place an order"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.placeOrder(order_params)
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None
    
    def modify_order(self, order_params):
        """Modify an existing order"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.modifyOrder(order_params)
        except Exception as e:
            logger.error(f"Error modifying order: {e}")
            return None
    
    def cancel_order(self, order_id, variety="NORMAL"):
        """Cancel an order"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.cancelOrder(order_id, variety)
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return None
    
    def get_order_book(self):
        """Get order book"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.orderBook()
        except Exception as e:
            logger.error(f"Error getting order book: {e}")
            return None
    
    def get_trade_book(self):
        """Get trade book"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            return self.smart_api.tradeBook()
        except Exception as e:
            logger.error(f"Error getting trade book: {e}")
            return None
    
    def get_live_data(self, symbol_token, exchange="NSE"):
        """Get live/real-time data for a symbol"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            # Use ltpData method with correct signature: (exchange, tradingsymbol, symboltoken)
            # For indices, tradingsymbol and symboltoken are usually the same
            print(f"Calling ltpData with exchange: {exchange}, symbol_token: {symbol_token}")
            result = self.smart_api.ltpData(exchange, symbol_token, symbol_token)
            print(f"ltpData result: {result}")
            return result
        except Exception as e:
            print(f"Error getting live data: {e}")
            return None
    
    def get_quote_data(self, symbol_token, exchange="NSE"):
        """Get detailed quote data for a symbol"""
        if not self.is_connected or not self.smart_api:
            return None
        try:
            # Use the getMarketData method for detailed quote data
            # Format: getMarketData(exchange, tradingsymbol, symboltoken)
            return self.smart_api.getMarketData(exchange, symbol_token, symbol_token)
        except Exception as e:
            logger.error(f"Error getting quote data: {e}")
            return None

    def generate_totp(self):
        """Generate TOTP using the secret from .env"""
        totp_secret = os.getenv('ANGEL_ONE_TOTP_SECRET')
        if not totp_secret:
            logger.error("TOTP secret not found in environment variables")
            return None
        totp = pyotp.TOTP(totp_secret)
        return totp.now()

    def get_access_token_direct(self):
        """Login to Angel One and get access token using direct HTTP requests"""
        try:
            # Generate TOTP
            totp = self.generate_totp()
            if not totp:
                return None
            
            # Get credentials
            api_key = os.getenv('ANGEL_ONE_API_KEY')
            client_id = os.getenv('ANGEL_ONE_CLIENT_ID')
            password = os.getenv('ANGEL_ONE_PASSWORD')
            
            # Login payload
            payload = {
                "clientcode": client_id,
                "password": password,
                "totp": totp
            }
            
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-UserType': 'USER',
                'X-SourceID': 'WEB',
                'X-ClientLocalIP': 'CLIENT_LOCAL_IP',
                'X-ClientPublicIP': 'CLIENT_PUBLIC_IP',
                'X-MACAddress': 'MAC_ADDRESS',
                'X-PrivateKey': api_key
            }
            
            # Make login request
            conn = http.client.HTTPSConnection("apiconnect.angelone.in")
            conn.request("POST", "/rest/auth/angelbroking/user/v1/loginByPassword", 
                        json.dumps(payload), headers)
            
            response = conn.getresponse()
            data = json.loads(response.read().decode())
            
            if data.get('status') and data.get('data'):
                access_token = data['data']['jwtToken']
                logger.info("Direct login successful!")
                return access_token
            else:
                logger.error(f"Direct login failed: {data.get('message', 'Unknown error')}")
                return None
                
        except Exception as e:
            logger.error(f"Direct login error: {e}")
            return None

    def fetch_market_data_direct(self, symbols):
        """
        Fetch comprehensive market data for given symbols using SmartAPI (enhanced approach)
        
        Parameters:
        symbols (dict): Dictionary of exchange to symbol tokens
        Example: {"NSE": ["3045", "99992000"], "BSE": ["500112"]}
        """
        try:
            if not self.is_connected or not self.smart_api:
                logger.error("SmartAPI not connected")
                return None
            
            # Use SmartAPI to get comprehensive market data
            # Convert symbols dict to the format expected by SmartAPI
            all_tokens = []
            for exchange, tokens in symbols.items():
                all_tokens.extend(tokens)
            
            # Get market data for all symbols
            market_data = {
                "status": True,
                "message": "SUCCESS",
                "data": {
                    "fetched": [],
                    "unfetched": []
                }
            }
            
            for token in all_tokens:
                try:
                    # Get comprehensive data for each symbol
                    # First get basic quote data
                    quote_data = self.smart_api.getMarketData("NSE", token)
                    
                    if quote_data and quote_data.get('data'):
                        data = quote_data['data']
                        
                        # Create comprehensive token data similar to direct API format
                        token_data = {
                            'symboltoken': token,
                            'tradingSymbol': data.get('tradingsymbol', f'TOKEN_{token}'),
                            'exchange': 'NSE',
                            'ltp': float(data.get('ltp', 0)),
                            'netChange': float(data.get('netChange', 0)),
                            'percentChange': float(data.get('percentChange', 0)),
                            'open': float(data.get('open', 0)),
                            'high': float(data.get('high', 0)),
                            'low': float(data.get('low', 0)),
                            'close': float(data.get('close', 0)),
                            'tradeVolume': int(data.get('volume', 0)),
                            '52WeekHigh': float(data.get('52WeekHigh', 0)),
                            '52WeekLow': float(data.get('52WeekLow', 0))
                        }
                        
                        market_data['data']['fetched'].append(token_data)
                        logger.info(f"Fetched data for token {token}: {token_data['tradingSymbol']}")
                        
                except Exception as e:
                    logger.warning(f"Failed to fetch data for token {token}: {e}")
                    market_data['data']['unfetched'].append(token)
            
            logger.info(f"Successfully fetched comprehensive data for {len(market_data['data']['fetched'])} symbols")
            return market_data
            
        except Exception as e:
            logger.error(f"Market data fetch error: {e}")
            return None

    def fetch_multiple_symbols_data(self, symbol_list):
        """
        Fetch market data for multiple symbols using comprehensive approach
        
        Parameters:
        symbol_list (list): List of symbol tokens to fetch
        """
        try:
            # Group symbols by exchange (assuming NSE for now)
            symbols = {"NSE": symbol_list}
            
            logger.info(f"Fetching comprehensive market data for {len(symbol_list)} symbols...")
            market_data = self.fetch_market_data_direct(symbols)
            
            if market_data:
                if market_data.get('status') and market_data.get('message') == 'SUCCESS':
                    logger.info("Successfully fetched comprehensive market data")
                    return market_data
                else:
                    logger.error(f"API Error: {market_data.get('message')}")
                    return None
            else:
                logger.error("Failed to fetch comprehensive market data")
                return None
                
        except Exception as e:
            logger.error(f"Error in fetch_multiple_symbols_data: {e}")
            return None

    def display_market_data(self, market_data):
        """Display comprehensive market data in a formatted way"""
        if not market_data or not market_data.get('data') or not market_data['data'].get('fetched'):
            logger.info("No market data available")
            return
        
        logger.info("\n" + "="*60)
        logger.info("COMPREHENSIVE MARKET DATA SUMMARY")
        logger.info("="*60)
        
        for token_data in market_data['data']['fetched']:
            logger.info(f"\n🔹 {token_data.get('tradingSymbol')} ({token_data.get('exchange')})")
            logger.info(f"   LTP: ₹{token_data.get('ltp')}")
            logger.info(f"   Change: ₹{token_data.get('netChange', 'N/A')} ({token_data.get('percentChange', 'N/A')}%)")
            logger.info(f"   Open: ₹{token_data.get('open')} | High: ₹{token_data.get('high')} | Low: ₹{token_data.get('low')}")
            logger.info(f"   Previous Close: ₹{token_data.get('close')}")
            logger.info(f"   Volume: {token_data.get('tradeVolume', 0):,} shares")
            logger.info(f"   52W High: ₹{token_data.get('52WeekHigh')} | 52W Low: ₹{token_data.get('52WeekLow')}")

    def generate_enhanced_signal(self, token_data: Dict) -> Dict:
        """Generate enhanced trading signal using comprehensive market data"""
        try:
            # Extract comprehensive data
            ltp = float(token_data.get('ltp', 0))
            open_price = float(token_data.get('open', 0))
            high = float(token_data.get('high', 0))
            low = float(token_data.get('low', 0))
            close = float(token_data.get('close', 0))
            volume = int(token_data.get('tradeVolume', 0))
            change_percent = float(token_data.get('percentChange', 0))
            week_52_high = float(token_data.get('52WeekHigh', 0))
            week_52_low = float(token_data.get('52WeekLow', 0))
            
            # Calculate enhanced metrics
            volume_score = min(volume / 1000000, 1.0)  # Volume score (0-1)
            volatility_score = (high - low) / ltp if ltp > 0 else 0
            trend_strength = abs(change_percent) / 100
            momentum_score = (ltp - open_price) / open_price if open_price > 0 else 0
            
            # Position relative to 52-week range
            week_52_range = week_52_high - week_52_low
            position_in_range = (ltp - week_52_low) / week_52_range if week_52_range > 0 else 0.5
            
            # Generate signal based on multiple factors
            signal_type = 'HOLD'
            confidence = 0.0
            
            # Enhanced signal logic for profitability
            if change_percent > 2.5 and volume_score > 1.5 and \
               volatility_score < 0.02 and trend_strength > 0.6:
                signal_type = 'BUY'
                confidence = min(0.8 + (change_percent - 2.5) * 0.05 + volume_score * 0.15, 0.95)
                
            elif change_percent < -2.5 and volume_score > 1.5 and \
                 volatility_score < 0.02 and trend_strength > 0.6:
                signal_type = 'SELL'
                confidence = min(0.8 + abs(change_percent + 2.5) * 0.05 + volume_score * 0.15, 0.95)
            
            # Adjust confidence based on position in 52-week range
            if position_in_range < 0.2 or position_in_range > 0.8:
                confidence *= 0.8  # Reduce confidence for extreme positions
            
            return {
                'signal': signal_type,
                'confidence': confidence,
                'entry_price': ltp,
                'volume_score': volume_score,
                'volatility_score': volatility_score,
                'trend_strength': trend_strength,
                'momentum_score': momentum_score,
                'position_in_52w_range': position_in_range,
                'change_percent': change_percent,
                'volume': volume,
                '52w_high': week_52_high,
                '52w_low': week_52_low,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f'Error generating enhanced signal: {str(e)}')
            return {
                'signal': 'HOLD',
                'confidence': 0.0,
                'entry_price': 0,
                'timestamp': datetime.now().isoformat()
            }

# Global instance
angel_one_client = AngelOneConnection()

def initialize_smartapi():
    """Initialize SmartAPI connection - wrapper function for backward compatibility"""
    return angel_one_client.initialize_smartapi()

if __name__ == "__main__":
    # Initialize API connection
    success = initialize_smartapi()
    
    if not success:
        logger.error("Initial connection failed. Trying with fresh TOTP...")
        # Try to reconnect with fresh TOTP
        success = angel_one_client.reconnect_with_fresh_totp()
    
    if success:
        try:
            # Test various API functions
            logger.info("Testing Angel One API functions...")
            
            # Get profile
            profile = angel_one_client.get_profile()
            if profile:
                logger.info(f"Profile: {profile['data']['name']}")
            
            # Get funds
            funds = angel_one_client.get_funds()
            if funds:
                logger.info(f"Funds: {funds}")
            
            # Get holdings
            holdings = angel_one_client.get_holdings()
            if holdings and holdings.get('data'):
                logger.info(f"Holdings: {len(holdings.get('data', []))} items")
            else:
                logger.info("Holdings: 0 items (no data or API error)")
            
            # Get positions
            positions = angel_one_client.get_positions()
            if positions and positions.get('data'):
                logger.info(f"Positions: {len(positions.get('data', []))} items")
            else:
                logger.info("Positions: 0 items (no data or API error)")
            
            logger.info("Angel One API connection test completed successfully!")
            
        except Exception as e:
            logger.error(f"Error in main execution: {e}")
    else:
        logger.error("Failed to initialize API connection")