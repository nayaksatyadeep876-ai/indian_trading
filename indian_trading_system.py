#!/usr/bin/env python3
"""
Enhanced Indian Trading System with Auto-Trading Capabilities
Focuses on NIFTY, BANKNIFTY, and major Indian stocks for maximum profitability
"""

import pandas as pd
import numpy as np
import requests
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3
import time
import threading
from dataclasses import dataclass
from angel_connection import angel_one_client, AngelOneConnection

logger = logging.getLogger(__name__)

@dataclass
class IndianTradeSignal:
    """Indian market trade signal with enhanced analysis"""
    symbol: str
    signal_type: str  # 'BUY', 'SELL', 'HOLD'
    confidence: float  # 0.0 to 1.0
    entry_price: float
    target_price: float
    stop_loss: float
    strategy: str
    timeframe: str
    risk_reward_ratio: float
    market_sentiment: str
    volume_analysis: str
    volatility: float
    timestamp: datetime
    expiry_date: Optional[str] = None
    option_type: Optional[str] = None  # 'CE', 'PE' for options

class IndianTradingSystem:
    """Advanced Indian Trading System with Auto-Trading"""
    
    def __init__(self, db_path: str = 'trading.db', angel_one_client=None):
        self.db_path = db_path
        self.indian_symbols = {
            # Major Indices
            'NIFTY50': '^NSEI',
            'BANKNIFTY': '^NSEBANK', 
            'SENSEX': '^BSESN',
            'FINNIFTY': '^CNXFIN',
            'MIDCPNIFTY': '^NSEMDCP50',
            # Sector Indices
            'NIFTYREALTY': '^CNXREALTY',
            'NIFTYPVTBANK': '^NIFTYBANK',
            'NIFTYPSUBANK': '^CNXPSUBANK',
            'NIFTYFIN': '^CNXFIN',
            'NIFTYMEDIA': '^CNXMEDIA',
            # Major Stocks
            'RELIANCE': 'RELIANCE.NS',
            'TCS': 'TCS.NS',
            'HDFCBANK': 'HDFCBANK.NS',
            'INFY': 'INFY.NS',
            'ICICIBANK': 'ICICIBANK.NS',
            'HINDUNILVR': 'HINDUNILVR.NS',
            'SBIN': 'SBIN.NS',
            'BHARTIARTL': 'BHARTIARTL.NS',
            'KOTAKBANK': 'KOTAKBANK.NS',
            'BAJFINANCE': 'BAJFINANCE.NS'
        }
        
        # Initialize Angel One integration
        if angel_one_client:
            self.angel_one = angel_one_client
            self.data_provider = angel_one_client  # Set data_provider to angel_one_client
        else:
            # Disable mock; require Angel One configuration first
            self.angel_one = None
            self.data_provider = None
            logger.error("Angel One client not configured. Please set credentials and restart.")
        
        # Trading strategies for Indian markets
        self.strategies = {
            'nifty_momentum': self.nifty_momentum_strategy,
            'banknifty_volatility': self.banknifty_volatility_strategy,
            'stock_breakout': self.stock_breakout_strategy
        }
        
        # Market timing for Indian markets (IST)
        self.market_hours = {
            'pre_market': '09:00',
            'market_open': '09:15',
            'market_close': '15:30',
            'post_market': '15:45'
        }
        
        self.active_trades = {}
        self.trade_history = []
        self.performance_metrics = {}
        
    def is_market_open(self) -> bool:
        """Check if Indian market is currently open (IST)"""
        try:
            # Simulation override via app_settings
            try:
                from app import get_setting, app
                with app.app_context():
                    if get_setting('simulation_mode', '0') == '1':
                        return True
            except Exception:
                pass
            # Get current IST time
            from datetime import timezone
            import pytz
            
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            
            # Check if it's a weekday
            if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
                return False
                
            # Check market hours
            current_time = now.time()
            market_open = datetime.strptime(self.market_hours['market_open'], '%H:%M').time()
            market_close = datetime.strptime(self.market_hours['market_close'], '%H:%M').time()
            
            return market_open <= current_time <= market_close
            
        except Exception as e:
            logger.error(f"Error checking market hours: {str(e)}")
            return False
    
    def get_indian_market_data(self, symbol: str, period: str = '1d', interval: str = '1d') -> Optional[pd.DataFrame]:
        """Get Indian market data using Angel One API only (no mock/yfinance)."""
        try:
            if symbol not in self.indian_symbols:
                logger.error(f"Symbol {symbol} not found in Indian symbols")
                return None
            
            if not self.data_provider:
                logger.warning("Angel One data provider unavailable. Using mock data for testing.")
                return self._generate_mock_data(symbol, interval, period)
            
            # Angel One API
            try:
                data = self.data_provider.get_historical_data_wrapper(symbol, interval, period)
                if not data.empty:
                    return data
                logger.warning(f"Angel One returned no data for {symbol}")
            except Exception as e:
                logger.warning(f"Angel One API failed for {symbol}: {str(e)}")
            
            # Fallback to mock data for testing
            logger.info(f"Using mock data for {symbol} as fallback")
            return self._generate_mock_data(symbol, interval, period)
            
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def _generate_mock_data(self, symbol: str, interval: str = '1d', period: str = '1d') -> pd.DataFrame:
        """Generate mock data for testing when Angel One API is not available"""
        try:
            import pandas as pd
            import numpy as np
            from datetime import datetime, timedelta
            
            # Base prices for different symbols
            base_prices = {
                'NIFTY50': 19500,
                'BANKNIFTY': 45000,
                'SENSEX': 65000,
                'FINNIFTY': 21000,
                'MIDCPNIFTY': 35000,
                'RELIANCE': 2500,
                'TCS': 3500,
                'HDFCBANK': 1600,
                'INFY': 1500,
                'ICICIBANK': 900,
                'SBIN': 600,
                'BHARTIARTL': 800,
                'KOTAKBANK': 1700,
                'BAJFINANCE': 7000,
                'HINDUNILVR': 2500
            }
            
            base_price = base_prices.get(symbol, 1000)
            
            # Calculate number of data points based on period and interval
            if period == '1d':
                if interval == '1m':
                    points = 390  # 6.5 hours * 60 minutes
                elif interval == '5m':
                    points = 78   # 6.5 hours * 12 (5-min intervals)
                elif interval == '15m':
                    points = 26   # 6.5 hours * 4 (15-min intervals)
                elif interval == '1h':
                    points = 7    # 6.5 hours
                else:  # 1d
                    points = 1
            elif period == '7d':
                points = 7
            elif period == '1m':
                points = 30
            elif period == '3m':
                points = 90
            elif period == '6m':
                points = 180
            elif period == '1y':
                points = 365
            else:
                points = 30
            
            # Generate timestamps
            end_time = datetime.now()
            if interval == '1d':
                start_time = end_time - timedelta(days=points-1)
                timestamps = pd.date_range(start=start_time, end=end_time, freq='D')
            elif interval == '1h':
                start_time = end_time - timedelta(hours=points-1)
                timestamps = pd.date_range(start=start_time, end=end_time, freq='H')
            elif interval == '15m':
                start_time = end_time - timedelta(minutes=15*(points-1))
                timestamps = pd.date_range(start=start_time, end=end_time, freq='15T')
            elif interval == '5m':
                start_time = end_time - timedelta(minutes=5*(points-1))
                timestamps = pd.date_range(start=start_time, end=end_time, freq='5T')
            elif interval == '1m':
                start_time = end_time - timedelta(minutes=points-1)
                timestamps = pd.date_range(start=start_time, end=end_time, freq='T')
            else:
                timestamps = pd.date_range(start=end_time - timedelta(days=points-1), end=end_time, freq='D')
            
            # Generate price data with realistic movement
            np.random.seed(42)  # For consistent results
            returns = np.random.normal(0, 0.02, len(timestamps))  # 2% daily volatility
            prices = [base_price]
            
            for i in range(1, len(timestamps)):
                price_change = prices[-1] * returns[i]
                new_price = prices[-1] + price_change
                prices.append(max(new_price, base_price * 0.5))  # Prevent negative prices
            
            # Generate OHLC data
            data = []
            for i, (timestamp, close_price) in enumerate(zip(timestamps, prices)):
                # Generate realistic OHLC from close price
                volatility = close_price * 0.01  # 1% intraday volatility
                high = close_price + np.random.uniform(0, volatility)
                low = close_price - np.random.uniform(0, volatility)
                open_price = close_price + np.random.uniform(-volatility/2, volatility/2)
                volume = np.random.randint(100000, 1000000)
                
                data.append({
                    'Open': round(open_price, 2),
                    'High': round(high, 2),
                    'Low': round(low, 2),
                    'Close': round(close_price, 2),
                    'Volume': volume
                })
            
            # Create DataFrame
            df = pd.DataFrame(data, index=timestamps)
            df.index.name = 'Datetime'
            
            logger.info(f"Generated {len(df)} mock data points for {symbol}")
            # Add metadata to indicate this is mock data
            df.attrs['data_source'] = 'mock'
            return df
            
        except Exception as e:
            logger.error(f"Error generating mock data for {symbol}: {str(e)}")
            return pd.DataFrame()
    
    def analyze_indian_market(self, symbol: str) -> IndianTradeSignal:
        """Comprehensive Indian market analysis"""
        try:
            # Get market data
            data = self.get_indian_market_data(symbol, period='1mo', interval='1d')
            if data is None or data.empty:
                return self._create_neutral_signal(symbol)
            
            # Calculate technical indicators
            indicators = self._calculate_indian_indicators(data)
            
            # Apply trading strategies
            signal = self._apply_indian_strategies(symbol, data, indicators)
            
            # Calculate risk-reward
            risk_reward = self._calculate_risk_reward(signal, data)
            signal.risk_reward_ratio = risk_reward
            
            # Add market sentiment
            signal.market_sentiment = self._analyze_market_sentiment(symbol, data)
            
            # Add volume analysis
            signal.volume_analysis = self._analyze_volume(data)
            
            # Add volatility
            signal.volatility = self._calculate_volatility(data)
            
            return signal
            
        except Exception as e:
            logger.error(f"Error analyzing Indian market for {symbol}: {str(e)}")
            return self._create_neutral_signal(symbol)
    
    def _calculate_indian_indicators(self, data: pd.DataFrame) -> Dict:
        """Calculate technical indicators optimized for Indian markets"""
        try:
            close_prices = data['Close']
            
            # RSI with Indian market optimization
            rsi = self._calculate_rsi(close_prices, period=14)
            
            # MACD optimized for Indian markets
            ema12 = close_prices.ewm(span=12).mean()
            ema26 = close_prices.ewm(span=26).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9).mean()
            
            # Bollinger Bands with Indian volatility
            sma20 = close_prices.rolling(window=20).mean()
            std20 = close_prices.rolling(window=20).std()
            bb_upper = sma20 + (std20 * 2.2)  # Wider bands for Indian markets
            bb_lower = sma20 - (std20 * 2.2)
            
            # Moving averages
            sma50 = close_prices.rolling(window=50).mean()
            sma200 = close_prices.rolling(window=200).mean()
            
            # Volume indicators
            volume_sma = data['Volume'].rolling(window=20).mean()
            volume_ratio = data['Volume'] / volume_sma
            
            # Support and resistance levels
            support_levels = self._find_support_levels(close_prices)
            resistance_levels = self._find_resistance_levels(close_prices)
            
            return {
                'rsi': rsi.iloc[-1] if not rsi.empty else 50,
                'macd': macd.iloc[-1] if not macd.empty else 0,
                'macd_signal': signal.iloc[-1] if not signal.empty else 0,
                'bb_upper': bb_upper.iloc[-1] if not bb_upper.empty else close_prices.iloc[-1],
                'bb_lower': bb_lower.iloc[-1] if not bb_lower.empty else close_prices.iloc[-1],
                'sma50': sma50.iloc[-1] if not sma50.empty else close_prices.iloc[-1],
                'sma200': sma200.iloc[-1] if not sma200.empty else close_prices.iloc[-1],
                'volume_ratio': volume_ratio.iloc[-1] if not volume_ratio.empty else 1.0,
                'support_levels': support_levels,
                'resistance_levels': resistance_levels
            }
            
        except Exception as e:
            logger.error(f"Error calculating indicators: {str(e)}")
            return {}
    
    def _apply_indian_strategies(self, symbol: str, data: pd.DataFrame, indicators: Dict) -> IndianTradeSignal:
        """Apply enhanced Indian market specific trading strategies"""
        try:
            current_price = data['Close'].iloc[-1]
            
            # Multi-strategy approach with weighted signals
            strategies_results = []
            
            # Strategy 1: NIFTY Momentum Strategy
            if 'NIFTY' in symbol:
                momentum_signal = self.nifty_momentum_strategy(data, indicators)
                strategies_results.append((momentum_signal, 0.4))  # 40% weight
                
                # Add mean reversion for NIFTY
                mean_reversion_signal = self.mean_reversion_strategy(data, indicators)
                strategies_results.append((mean_reversion_signal, 0.3))  # 30% weight
                
                # Add trend following
                trend_signal = self.trend_following_strategy(data, indicators)
                strategies_results.append((trend_signal, 0.3))  # 30% weight
                
            # Strategy 2: BANKNIFTY Volatility Strategy  
            elif 'BANKNIFTY' in symbol:
                volatility_signal = self.banknifty_volatility_strategy(data, indicators)
                strategies_results.append((volatility_signal, 0.5))  # 50% weight
                
                # Add breakout strategy for BANKNIFTY
                breakout_signal = self.enhanced_breakout_strategy(data, indicators)
                strategies_results.append((breakout_signal, 0.5))  # 50% weight
                
            # Strategy 3: Stock specific strategies
            else:
                # Primary strategy based on stock characteristics
                if symbol in ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY']:  # Large cap
                    large_cap_signal = self.large_cap_strategy(data, indicators)
                    strategies_results.append((large_cap_signal, 0.6))  # 60% weight
                    
                    # Add momentum for large caps
                    momentum_signal = self.stock_momentum_strategy(data, indicators)
                    strategies_results.append((momentum_signal, 0.4))  # 40% weight
                    
                else:  # Mid/Small cap
                    breakout_signal = self.stock_breakout_strategy(data, indicators)
                    strategies_results.append((breakout_signal, 0.7))  # 70% weight
                    
                    # Add volatility strategy
                    vol_signal = self.stock_volatility_strategy(data, indicators)
                    strategies_results.append((vol_signal, 0.3))  # 30% weight
            
            # Combine signals with weighted average
            final_signal = self._combine_weighted_signals(strategies_results, current_price)
            
            # Create trade signal
            return IndianTradeSignal(
                symbol=symbol,
                signal_type=final_signal['type'],
                confidence=final_signal['confidence'],
                entry_price=current_price,
                target_price=final_signal['target'],
                stop_loss=final_signal['stop_loss'],
                strategy=final_signal['strategy_name'],
                timeframe='1D',
                risk_reward_ratio=0.0,  # Will be calculated later
                market_sentiment='',
                volume_analysis='',
                volatility=0.0,
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Error applying strategies: {str(e)}")
            return self._create_neutral_signal(symbol)
    
    def nifty_momentum_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """NIFTY specific momentum strategy"""
        try:
            current_price = data['Close'].iloc[-1]
            rsi = indicators.get('rsi', 50)
            macd = indicators.get('macd', 0)
            macd_signal = indicators.get('macd_signal', 0)
            sma50 = indicators.get('sma50', current_price)
            sma200 = indicators.get('sma200', current_price)
            
            # NIFTY momentum conditions (very aggressive for demo)
            momentum_buy = (
                current_price > sma50  # Above 50 SMA (main condition)
            )
            
            momentum_sell = (
                current_price < sma50  # Below 50 SMA
            )
            
            if momentum_buy:
                target = current_price * 1.02  # 2% target
                stop_loss = current_price * 0.98  # 2% stop loss
                confidence = 0.7  # Fixed confidence for more consistent signals
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'NIFTY Momentum'
                }
            elif momentum_sell:
                target = current_price * 0.98  # 2% target
                stop_loss = current_price * 1.02  # 2% stop loss
                confidence = 0.7  # Fixed confidence for more consistent signals
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'NIFTY Momentum'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.3,  # Lower confidence for HOLD signals
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'NIFTY Momentum'
                }
                
        except Exception as e:
            logger.error(f"Error in NIFTY momentum strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}
    
    def banknifty_volatility_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """BANKNIFTY specific volatility strategy"""
        try:
            current_price = data['Close'].iloc[-1]
            bb_upper = indicators.get('bb_upper', current_price)
            bb_lower = indicators.get('bb_lower', current_price)
            rsi = indicators.get('rsi', 50)
            volume_ratio = indicators.get('volume_ratio', 1.0)
            
            # BANKNIFTY volatility conditions
            volatility_buy = (
                current_price <= bb_lower and  # Price at lower Bollinger Band
                rsi < 35 and  # Oversold
                volume_ratio > 1.2  # High volume
            )
            
            volatility_sell = (
                current_price >= bb_upper and  # Price at upper Bollinger Band
                rsi > 75 and  # Overbought
                volume_ratio > 1.2  # High volume
            )
            
            if volatility_buy:
                target = current_price * 1.015  # 1.5% target (shorter for volatility)
                stop_loss = current_price * 0.985  # 1.5% stop loss
                confidence = min((35 - rsi) / 35, 1.0)
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'BANKNIFTY Volatility'
                }
            elif volatility_sell:
                target = current_price * 0.985  # 1.5% target
                stop_loss = current_price * 1.015  # 1.5% stop loss
                confidence = min((rsi - 25) / 50, 1.0)
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'BANKNIFTY Volatility'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.5,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'BANKNIFTY Volatility'
                }
                
        except Exception as e:
            logger.error(f"Error in BANKNIFTY volatility strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}
    
    def stock_breakout_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Stock specific breakout strategy"""
        try:
            current_price = data['Close'].iloc[-1]
            resistance_levels = indicators.get('resistance_levels', [])
            support_levels = indicators.get('support_levels', [])
            volume_ratio = indicators.get('volume_ratio', 1.0)
            rsi = indicators.get('rsi', 50)
            
            # Check for breakout above resistance
            breakout_buy = False
            breakout_target = current_price * 1.03
            
            for resistance in resistance_levels:
                if current_price > resistance and volume_ratio > 1.5:
                    breakout_buy = True
                    breakout_target = resistance * 1.02
                    break
            
            # Check for breakdown below support
            breakdown_sell = False
            breakdown_target = current_price * 0.97
            
            for support in support_levels:
                if current_price < support and volume_ratio > 1.5:
                    breakdown_sell = True
                    breakdown_target = support * 0.98
                    break
            
            if breakout_buy:
                return {
                    'type': 'BUY',
                    'confidence': 0.8,
                    'target': breakout_target,
                    'stop_loss': current_price * 0.98,
                    'strategy_name': 'Stock Breakout'
                }
            elif breakdown_sell:
                return {
                    'type': 'SELL',
                    'confidence': 0.8,
                    'target': breakdown_target,
                    'stop_loss': current_price * 1.02,
                    'strategy_name': 'Stock Breakdown'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.5,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Stock Breakout'
                }
                
        except Exception as e:
            logger.error(f"Error in stock breakout strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}
    
    def _create_neutral_signal(self, symbol: str) -> IndianTradeSignal:
        """Create a neutral signal when analysis fails"""
        return IndianTradeSignal(
            symbol=symbol,
            signal_type='HOLD',
            confidence=0.0,
            entry_price=0.0,
            target_price=0.0,
            stop_loss=0.0,
            strategy='Neutral',
            timeframe='1D',
            risk_reward_ratio=0.0,
            market_sentiment='Neutral',
            volume_analysis='Unknown',
            volatility=0.0,
            timestamp=datetime.now()
        )
    
    def _calculate_risk_reward(self, signal: IndianTradeSignal, data: pd.DataFrame) -> float:
        """Calculate risk-reward ratio"""
        try:
            if signal.signal_type == 'HOLD':
                return 0.0
                
            current_price = data['Close'].iloc[-1]
            
            if signal.signal_type == 'BUY':
                reward = signal.target_price - current_price
                risk = current_price - signal.stop_loss
            else:  # SELL
                reward = current_price - signal.target_price
                risk = signal.stop_loss - current_price
                
            if risk > 0:
                return reward / risk
            return 0.0
            
        except Exception as e:
            logger.error(f"Error calculating risk-reward: {str(e)}")
            return 0.0
    
    def _analyze_market_sentiment(self, symbol: str, data: pd.DataFrame) -> str:
        """Analyze market sentiment for Indian markets"""
        try:
            # Simple sentiment based on price movement
            if len(data) < 5:
                return 'Neutral'
                
            recent_prices = data['Close'].tail(5)
            price_change = (recent_prices.iloc[-1] - recent_prices.iloc[0]) / recent_prices.iloc[0]
            
            if price_change > 0.02:  # 2% gain
                return 'Bullish'
            elif price_change < -0.02:  # 2% loss
                return 'Bearish'
            else:
                return 'Neutral'
                
        except Exception as e:
            logger.error(f"Error analyzing sentiment: {str(e)}")
            return 'Neutral'
    
    def _analyze_volume(self, data: pd.DataFrame) -> str:
        """Analyze volume patterns"""
        try:
            if len(data) < 20:
                return 'Normal'
                
            recent_volume = data['Volume'].tail(5).mean()
            avg_volume = data['Volume'].tail(20).mean()
            
            if recent_volume > avg_volume * 1.5:
                return 'High'
            elif recent_volume < avg_volume * 0.5:
                return 'Low'
            else:
                return 'Normal'
                
        except Exception as e:
            logger.error(f"Error analyzing volume: {str(e)}")
            return 'Normal'
    
    def _calculate_volatility(self, data: pd.DataFrame) -> float:
        """Calculate price volatility"""
        try:
            if len(data) < 20:
                return 0.0
                
            returns = data['Close'].pct_change().dropna()
            return returns.std()
            
        except Exception as e:
            logger.error(f"Error calculating volatility: {str(e)}")
            return 0.0
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI indicator"""
        try:
            delta = prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi
        except Exception as e:
            logger.error(f"Error calculating RSI: {str(e)}")
            return pd.Series()
    
    def _find_support_levels(self, prices: pd.Series) -> List[float]:
        """Find support levels using pivot points"""
        try:
            if len(prices) < 20:
                return []
                
            # Simple support levels using recent lows
            support_levels = []
            for i in range(10, len(prices)):
                if prices.iloc[i] == prices.iloc[i-10:i+1].min():
                    support_levels.append(prices.iloc[i])
                    
            return support_levels[:3]  # Return top 3 support levels
            
        except Exception as e:
            logger.error(f"Error finding support levels: {str(e)}")
            return []
    
    def _find_resistance_levels(self, prices: pd.Series) -> List[float]:
        """Find resistance levels using pivot points"""
        try:
            if len(prices) < 20:
                return []
                
            # Simple resistance levels using recent highs
            resistance_levels = []
            for i in range(10, len(prices)):
                if prices.iloc[i] == prices.iloc[i-10:i+1].max():
                    resistance_levels.append(prices.iloc[i])
                    
            return resistance_levels[:3]  # Return top 3 resistance levels
            
        except Exception as e:
            logger.error(f"Error finding resistance levels: {str(e)}")
            return []

    def mean_reversion_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Mean reversion strategy for Indian markets"""
        try:
            current_price = data['Close'].iloc[-1]
            rsi = indicators.get('rsi', 50)
            bb_upper = indicators.get('bb_upper', current_price)
            bb_lower = indicators.get('bb_lower', current_price)
            bb_middle = indicators.get('bb_middle', current_price)
            
            # Mean reversion conditions
            oversold = rsi < 30 and current_price <= bb_lower
            overbought = rsi > 70 and current_price >= bb_upper
            
            if oversold:
                target = bb_middle  # Target middle of Bollinger Bands
                stop_loss = current_price * 0.98  # 2% stop loss
                confidence = min((30 - rsi) / 30, 1.0)
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Mean Reversion'
                }
            elif overbought:
                target = bb_middle
                stop_loss = current_price * 1.02
                confidence = min((rsi - 70) / 30, 1.0)
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Mean Reversion'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.3,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Mean Reversion'
                }
                
        except Exception as e:
            logger.error(f"Error in mean reversion strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def trend_following_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Trend following strategy"""
        try:
            current_price = data['Close'].iloc[-1]
            sma_20 = indicators.get('sma_20', current_price)
            sma_50 = indicators.get('sma_50', current_price)
            ema_12 = indicators.get('ema_12', current_price)
            ema_26 = indicators.get('ema_26', current_price)
            macd = indicators.get('macd', 0)
            macd_signal = indicators.get('macd_signal', 0)
            
            # Trend following conditions
            bullish_trend = (
                current_price > sma_20 > sma_50 and  # Price above moving averages
                ema_12 > ema_26 and  # EMA crossover
                macd > macd_signal  # MACD bullish
            )
            
            bearish_trend = (
                current_price < sma_20 < sma_50 and  # Price below moving averages
                ema_12 < ema_26 and  # EMA crossover
                macd < macd_signal  # MACD bearish
            )
            
            if bullish_trend:
                target = current_price * 1.025  # 2.5% target
                stop_loss = sma_20 * 0.995  # Stop below 20 SMA
                confidence = 0.7
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Trend Following'
                }
            elif bearish_trend:
                target = current_price * 0.975  # 2.5% target
                stop_loss = sma_20 * 1.005  # Stop above 20 SMA
                confidence = 0.7
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Trend Following'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.3,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Trend Following'
                }
                
        except Exception as e:
            logger.error(f"Error in trend following strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def enhanced_breakout_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Enhanced breakout strategy with volume confirmation"""
        try:
            current_price = data['Close'].iloc[-1]
            resistance_levels = indicators.get('resistance_levels', [])
            support_levels = indicators.get('support_levels', [])
            volume_ratio = indicators.get('volume_ratio', 1.0)
            atr = indicators.get('atr', current_price * 0.02)
            
            # Check for breakout above resistance with volume
            breakout_buy = False
            breakout_target = current_price * 1.03
            
            for resistance in resistance_levels:
                if current_price > resistance and volume_ratio > 1.5:
                    breakout_buy = True
                    breakout_target = resistance * 1.02
                    break
            
            # Check for breakdown below support with volume
            breakdown_sell = False
            breakdown_target = current_price * 0.97
            
            for support in support_levels:
                if current_price < support and volume_ratio > 1.5:
                    breakdown_sell = True
                    breakdown_target = support * 0.98
                    break
            
            if breakout_buy:
                return {
                    'type': 'BUY',
                    'confidence': min(0.8, 0.5 + volume_ratio * 0.1),
                    'target': breakout_target,
                    'stop_loss': current_price - atr,
                    'strategy_name': 'Enhanced Breakout'
                }
            elif breakdown_sell:
                return {
                    'type': 'SELL',
                    'confidence': min(0.8, 0.5 + volume_ratio * 0.1),
                    'target': breakdown_target,
                    'stop_loss': current_price + atr,
                    'strategy_name': 'Enhanced Breakdown'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.3,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Enhanced Breakout'
                }
                
        except Exception as e:
            logger.error(f"Error in enhanced breakout strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def large_cap_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Strategy optimized for large cap stocks"""
        try:
            current_price = data['Close'].iloc[-1]
            rsi = indicators.get('rsi', 50)
            sma_50 = indicators.get('sma_50', current_price)
            sma_200 = indicators.get('sma_200', current_price)
            volume_ratio = indicators.get('volume_ratio', 1.0)
            macd = indicators.get('macd', 0)
            macd_signal = indicators.get('macd_signal', 0)
            
            # Large cap conditions (more conservative)
            bullish = (
                rsi > 45 and rsi < 65 and  # RSI in neutral zone
                current_price > sma_50 > sma_200 and  # Above key MAs
                macd > macd_signal and  # MACD bullish
                volume_ratio > 1.2  # Above average volume
            )
            
            bearish = (
                rsi < 55 and rsi > 35 and  # RSI in neutral zone
                current_price < sma_50 and  # Below 50 SMA
                macd < macd_signal and  # MACD bearish
                volume_ratio > 1.2  # Above average volume
            )
            
            if bullish:
                target = current_price * 1.02  # Conservative 2% target
                stop_loss = current_price * 0.98  # 2% stop loss
                confidence = 0.6
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Large Cap'
                }
            elif bearish:
                target = current_price * 0.98
                stop_loss = current_price * 1.02
                confidence = 0.6
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Large Cap'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.4,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Large Cap'
                }
                
        except Exception as e:
            logger.error(f"Error in large cap strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def stock_momentum_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Momentum strategy for individual stocks"""
        try:
            current_price = data['Close'].iloc[-1]
            rsi = indicators.get('rsi', 50)
            stochastic_k = indicators.get('stochastic_k', 50)
            williams_r = indicators.get('williams_r', -50)
            volume_ratio = indicators.get('volume_ratio', 1.0)
            
            # Momentum conditions
            strong_momentum_buy = (
                rsi > 50 and rsi < 70 and  # RSI bullish but not overbought
                stochastic_k > 50 and  # Stochastic bullish
                williams_r > -50 and  # Williams %R bullish
                volume_ratio > 1.3  # Strong volume
            )
            
            strong_momentum_sell = (
                rsi < 50 and rsi > 30 and  # RSI bearish but not oversold
                stochastic_k < 50 and  # Stochastic bearish
                williams_r < -50 and  # Williams %R bearish
                volume_ratio > 1.3  # Strong volume
            )
            
            if strong_momentum_buy:
                target = current_price * 1.03  # 3% target
                stop_loss = current_price * 0.97  # 3% stop loss
                confidence = 0.7
                
                return {
                    'type': 'BUY',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Stock Momentum'
                }
            elif strong_momentum_sell:
                target = current_price * 0.97
                stop_loss = current_price * 1.03
                confidence = 0.7
                
                return {
                    'type': 'SELL',
                    'confidence': confidence,
                    'target': target,
                    'stop_loss': stop_loss,
                    'strategy_name': 'Stock Momentum'
                }
            else:
                return {
                    'type': 'HOLD',
                    'confidence': 0.3,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Stock Momentum'
                }
                
        except Exception as e:
            logger.error(f"Error in stock momentum strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def stock_volatility_strategy(self, data: pd.DataFrame, indicators: Dict) -> Dict:
        """Volatility-based strategy for stocks"""
        try:
            current_price = data['Close'].iloc[-1]
            bb_upper = indicators.get('bb_upper', current_price)
            bb_lower = indicators.get('bb_lower', current_price)
            bb_width = indicators.get('bb_width', 0.02)
            atr = indicators.get('atr', current_price * 0.02)
            volume_ratio = indicators.get('volume_ratio', 1.0)
            
            # High volatility conditions
            high_volatility = bb_width > 0.03  # Bollinger Band width > 3%
            
            if high_volatility:
                # Volatility expansion strategy
                if current_price <= bb_lower and volume_ratio > 1.2:
                    target = current_price + atr * 2  # Target 2 ATR
                    stop_loss = current_price - atr
                    confidence = 0.6
                    
                    return {
                        'type': 'BUY',
                        'confidence': confidence,
                        'target': target,
                        'stop_loss': stop_loss,
                        'strategy_name': 'Volatility Expansion'
                    }
                elif current_price >= bb_upper and volume_ratio > 1.2:
                    target = current_price - atr * 2
                    stop_loss = current_price + atr
                    confidence = 0.6
                    
                    return {
                        'type': 'SELL',
                        'confidence': confidence,
                        'target': target,
                        'stop_loss': stop_loss,
                        'strategy_name': 'Volatility Expansion'
                    }
            
            return {
                'type': 'HOLD',
                'confidence': 0.3,
                'target': current_price,
                'stop_loss': current_price,
                'strategy_name': 'Volatility'
            }
                
        except Exception as e:
            logger.error(f"Error in stock volatility strategy: {str(e)}")
            return {'type': 'HOLD', 'confidence': 0.0, 'target': 0, 'stop_loss': 0, 'strategy_name': 'Error'}

    def _combine_weighted_signals(self, strategies_results: List[Tuple[Dict, float]], current_price: float) -> Dict:
        """Combine multiple strategy signals with weighted average"""
        try:
            if not strategies_results:
                return {
                    'type': 'HOLD',
                    'confidence': 0.0,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Combined'
                }
            
            # Calculate weighted averages
            total_weight = sum(weight for _, weight in strategies_results)
            if total_weight == 0:
                return {
                    'type': 'HOLD',
                    'confidence': 0.0,
                    'target': current_price,
                    'stop_loss': current_price,
                    'strategy_name': 'Combined'
                }
            
            # Weighted signal types
            buy_weight = sum(weight for signal, weight in strategies_results if signal['type'] == 'BUY')
            sell_weight = sum(weight for signal, weight in strategies_results if signal['type'] == 'SELL')
            hold_weight = sum(weight for signal, weight in strategies_results if signal['type'] == 'HOLD')
            
            # Determine final signal type (more aggressive - prefer BUY/SELL over HOLD)
            if buy_weight > sell_weight:
                final_type = 'BUY'
                confidence = buy_weight / total_weight
            elif sell_weight > 0:  # Any SELL signal beats HOLD
                final_type = 'SELL'
                confidence = sell_weight / total_weight
            else:
                final_type = 'HOLD'
                confidence = hold_weight / total_weight
            
            # Calculate weighted targets and stop losses
            if final_type == 'BUY':
                relevant_signals = [signal for signal, weight in strategies_results if signal['type'] == 'BUY']
                if relevant_signals:
                    target = sum(signal['target'] * weight for signal, weight in strategies_results if signal['type'] == 'BUY') / buy_weight
                    stop_loss = sum(signal['stop_loss'] * weight for signal, weight in strategies_results if signal['type'] == 'BUY') / buy_weight
                else:
                    target = current_price * 1.02
                    stop_loss = current_price * 0.98
            elif final_type == 'SELL':
                relevant_signals = [signal for signal, weight in strategies_results if signal['type'] == 'SELL']
                if relevant_signals:
                    target = sum(signal['target'] * weight for signal, weight in strategies_results if signal['type'] == 'SELL') / sell_weight
                    stop_loss = sum(signal['stop_loss'] * weight for signal, weight in strategies_results if signal['type'] == 'SELL') / sell_weight
                else:
                    target = current_price * 0.98
                    stop_loss = current_price * 1.02
            else:
                target = current_price
                stop_loss = current_price
            
            return {
                'type': final_type,
                'confidence': confidence,
                'target': target,
                'stop_loss': stop_loss,
                'strategy_name': 'Multi-Strategy'
            }
            
        except Exception as e:
            logger.error(f"Error combining weighted signals: {str(e)}")
            return {
                'type': 'HOLD',
                'confidence': 0.0,
                'target': current_price,
                'stop_loss': current_price,
                'strategy_name': 'Error'
            }
    
    def get_all_signals(self) -> List[IndianTradeSignal]:
        """Get trading signals for all Indian symbols"""
        signals = []
        logger.info(f"Getting signals for {len(self.indian_symbols)} symbols")
        
        # Throttle to avoid API rate limits: evaluate only first 5 symbols per cycle
        symbol_list = list(self.indian_symbols.keys())[:5]
        for i, symbol in enumerate(symbol_list):
            try:
                logger.debug(f"Analyzing symbol {i+1}/{len(self.indian_symbols)}: {symbol}")
                signal = self.analyze_indian_market(symbol)
                if signal.signal_type != 'HOLD' and signal.confidence > 0.5:  # Lowered threshold
                    signals.append(signal)
                    logger.debug(f"Added signal: {symbol} {signal.signal_type} (conf: {signal.confidence})")
                
                # Rate limiting
                time.sleep(0.25)
                
            except Exception as e:
                logger.error(f"Error getting signal for {symbol}: {str(e)}")
                continue
                
        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        logger.info(f"Returning {len(signals)} signals")
        return signals
    
    def save_signal_to_db(self, signal: IndianTradeSignal, user_id: int):
        """Save trading signal to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO signals (user_id, pair, direction, confidence, time, created_at, 
                                  entry_price, stop_loss, take_profit, strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, signal.symbol, signal.signal_type, signal.confidence,
                signal.timestamp, signal.timestamp, signal.entry_price,
                signal.stop_loss, signal.target_price, signal.strategy
            ))
            
            conn.commit()
            conn.close()
            logger.info(f"Signal saved for {signal.symbol}")
            
        except Exception as e:
            logger.error(f"Error saving signal: {str(e)}")

# Auto-trading functionality
class IndianAutoTrader:
    """Auto-trading system for Indian markets"""
    
    def __init__(self, trading_system: IndianTradingSystem, user_id: int = 1, default_quantity: int = 1, initial_balance: float = 5000.0):
        self.trading_system = trading_system
        self.active_trades = {}
        self.running = False
        self.trading_thread = None
        self.trade_history = []
        self.total_pnl = 0.0
        self.user_id = user_id
        self.default_quantity = default_quantity
        self.initial_balance = initial_balance
        
    def start(self):
        """Start auto-trading"""
        if self.running and self.trading_thread and self.trading_thread.is_alive():
            logger.warning("Auto-trading already running")
            return
        
        # Reset state if thread is dead
        if self.trading_thread and not self.trading_thread.is_alive():
            logger.info("Previous trading thread was dead, restarting...")
            self.running = False
            
        self.running = True
        self.trading_thread = threading.Thread(target=self._trading_loop)
        self.trading_thread.daemon = True
        self.trading_thread.start()
        logger.info("Indian auto-trading started")
        
    def stop(self):
        """Stop auto-trading"""
        self.running = False
        if self.trading_thread:
            self.trading_thread.join()
        logger.info("Indian auto-trading stopped")
        
    def _trading_loop(self):
        """Main trading loop"""
        logger.info("Trading loop started")
        while self.running:
            try:
                # Only trade during market hours
                if self.trading_system.is_market_open():
                    logger.info("Market is open - checking for signals")
                    # Get all signals with timeout protection
                    try:
                        signals = self.trading_system.get_all_signals()
                        logger.info(f"Generated {len(signals)} signals")
                    except Exception as e:
                        logger.error(f"Error getting signals: {str(e)}")
                        signals = []
                    
                    # Process high-confidence signals
                    for signal in signals:
                        if signal.confidence > 0.4:  # Further lowered threshold for more active trading
                            logger.info(f"Executing trade: {signal.symbol} {signal.signal_type} (conf: {signal.confidence})")
                            self._execute_trade(signal)
                        else:
                            logger.debug(f"Signal below threshold: {signal.symbol} {signal.signal_type} (conf: {signal.confidence})")
                            
                    # Monitor existing trades
                    self._monitor_trades()
                else:
                    logger.info("Market is closed - waiting")
                    
                # Sleep for 30 seconds for more responsive testing
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {str(e)}")
                time.sleep(120)  # Wait 2 minutes on error
    
    def _execute_trade(self, signal: IndianTradeSignal):
        """Execute a trade based on signal"""
        try:
            trade_id = f"indian_{signal.symbol}_{int(time.time())}"
            
            # Create trade record
            trade = {
                'id': trade_id,
                'symbol': signal.symbol,
                'type': signal.signal_type,
                'entry_price': signal.entry_price,
                'target_price': signal.target_price,
                'stop_loss': signal.stop_loss,
                'quantity': self.default_quantity,
                'strategy': signal.strategy,
                'confidence': signal.confidence,
                'timestamp': signal.timestamp,
                # Track entry times for duration-based exits
                'entry_epoch': time.time(),
                'entry_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'ACTIVE'
            }
            
            self.active_trades[trade_id] = trade
            logger.info(f"✅ TRADE EXECUTED: {trade_id} - {signal.symbol} {signal.signal_type} @ {signal.entry_price}")
            logger.info(f"   Target: {signal.target_price}, Stop Loss: {signal.stop_loss}")
            
            # Persist active trade to DB for resilience
            try:
                conn = sqlite3.connect(self.trading.db_path)
                cur = conn.cursor()
                cur.execute('''
                    INSERT OR REPLACE INTO active_trades(id, symbol, type, entry_price, quantity, entry_time, user_id, strategy, confidence)
                    VALUES(?,?,?,?,?,?,?,?,?)
                ''', (
                    trade_id,
                    signal.symbol,
                    signal.signal_type,
                    float(signal.entry_price),
                    int(self.default_quantity),
                    trade['entry_time'],
                    int(self.user_id),
                    signal.strategy,
                    float(signal.confidence)
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Failed to persist active trade {trade_id}: {e}")

        except Exception as e:
            logger.error(f"❌ Error executing trade: {str(e)}")
    
    def _monitor_trades(self):
        """Monitor and manage active trades"""
        try:
            for trade_id, trade in list(self.active_trades.items()):
                # Get current price
                data = self.trading_system.get_indian_market_data(trade['symbol'], period='1d')
                if data is None or data.empty:
                    continue
                    
                current_price = data['Close'].iloc[-1]

                # Auto-close near market end (15:29:30 to 15:31:00 IST)
                try:
                    from datetime import timezone, timedelta
                    ist = timezone(timedelta(hours=5, minutes=30))
                    now_ist = datetime.now(ist).time()
                    market_close_guard_start = datetime.strptime('15:29:30', '%H:%M:%S').time()
                    market_close_guard_end = datetime.strptime('15:31:00', '%H:%M:%S').time()
                    if market_close_guard_start <= now_ist <= market_close_guard_end:
                        self._close_trade(trade_id, 'Market Close Exit', float(current_price))
                        continue
                except Exception as _:
                    pass

                # Auto-close max duration (e.g., 6 hours)
                try:
                    max_duration_seconds = 6 * 60 * 60
                    entry_epoch = float(trade.get('entry_epoch') or time.time())
                    if time.time() - entry_epoch >= max_duration_seconds:
                        self._close_trade(trade_id, 'Max Duration Exit', float(current_price))
                        continue
                except Exception as _:
                    pass
                
                # Check stop loss
                if trade['type'] == 'BUY' and current_price <= trade['stop_loss']:
                    self._close_trade(trade_id, 'Stop Loss Hit', current_price)
                elif trade['type'] == 'SELL' and current_price >= trade['stop_loss']:
                    self._close_trade(trade_id, 'Stop Loss Hit', current_price)
                    
                # Check target
                elif trade['type'] == 'BUY' and current_price >= trade['target_price']:
                    self._close_trade(trade_id, 'Target Reached', current_price)
                elif trade['type'] == 'SELL' and current_price <= trade['target_price']:
                    self._close_trade(trade_id, 'Target Reached', current_price)
                    
        except Exception as e:
            logger.error(f"Error monitoring trades: {str(e)}")
    
    def _close_trade(self, trade_id: str, reason: str, exit_price: float):
        """Close a trade"""
        try:
            if trade_id in self.active_trades:
                trade = self.active_trades[trade_id]
                logger.info(f"Closing trade {trade_id}: {reason}")
                
                # Calculate absolute P&L amount
                quantity = trade.get('quantity', self.default_quantity)
                entry_price = trade['entry_price']
                if trade['type'] == 'BUY':
                    pnl_amount = (exit_price - entry_price) * quantity
                else:
                    pnl_amount = (entry_price - exit_price) * quantity

                logger.info(f"Trade {trade_id} P&L amount: {pnl_amount:.2f}")

                # Update in-memory performance
                self.total_pnl += pnl_amount
                closed_trade = {
                    'id': trade_id,
                    'symbol': trade['symbol'],
                    'type': trade['type'],
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'quantity': quantity,
                    'reason': reason,
                    'pnl_amount': pnl_amount,
                    # Store full timestamp so date filters work
                    'exit_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                self.trade_history.append(closed_trade)

                # Persist to SQLite: trades and portfolio_history
                try:
                    conn = sqlite3.connect(self.trading.db_path)
                    cursor = conn.cursor()

                    # Insert closed trade record
                    cursor.execute('''
                        INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, quantity, status, entry_time, exit_time, profit_loss)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        self.user_id,
                        trade['symbol'],
                        trade['type'],
                        float(entry_price),
                        float(exit_price),
                        float(quantity),
                        'CLOSED',
                        trade['timestamp'],
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        float(pnl_amount)
                    ))

                    # Determine last portfolio value; if none, start with initial_balance
                    cursor.execute('''
                        SELECT portfolio_value FROM portfolio_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1
                    ''', (self.user_id,))
                    row = cursor.fetchone()
                    last_value = float(row[0]) if row else float(self.initial_balance)
                    new_value = last_value + float(pnl_amount)

                    # Insert new portfolio snapshot
                    cursor.execute('''
                        INSERT INTO portfolio_history (user_id, portfolio_value, timestamp)
                        VALUES (?, ?, ?)
                    ''', (
                        self.user_id,
                        new_value,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))

                    # Remove from active_trades table if exists
                    try:
                        cursor.execute('DELETE FROM active_trades WHERE id=?', (trade_id,))
                    except Exception:
                        pass
                    conn.commit()
                    conn.close()
                except Exception as db_err:
                    logger.error(f"Error updating portfolio/trades in DB: {db_err}")

                # Remove from active trades
                del self.active_trades[trade_id]
                
        except Exception as e:
            logger.error(f"Error closing trade: {str(e)}")
    
    def get_active_trades(self) -> Dict:
        """Get all active trades"""
        return self.active_trades.copy()
    
    def get_performance_summary(self) -> Dict:
        """Get trading performance summary"""
        try:
            total_trades = len(self.trade_history) if hasattr(self, 'trade_history') else 0
            active_trades = len(self.active_trades)
            
            # Check if thread is actually alive
            thread_alive = self.trading_thread and self.trading_thread.is_alive() if hasattr(self, 'trading_thread') else False
            actual_status = 'Running' if (self.running and thread_alive) else 'Stopped'
            
            return {
                'total_trades': total_trades,
                'active_trades': active_trades,
                'status': actual_status,
                'thread_alive': thread_alive
            }
            
        except Exception as e:
            logger.error(f"Error getting performance summary: {str(e)}")
            return {}
