import threading
import time
from datetime import datetime
import logging
from typing import Dict, List, Optional
import sqlite3
from trading_system import TradingSystem
from risk_manager import RiskManager
# from database_config import Database  # Commented out as it's not needed for basic functionality
from symbols import get_all_symbols, get_working_symbols
from angel_connection import angel_one_client

logger = logging.getLogger(__name__)

class AutoTrader:
    def __init__(self, trading_system: TradingSystem, risk_manager: RiskManager):
        self.trading_system = trading_system
        self.risk_manager = risk_manager
        self.active_trades = {}
        self.db_path = 'trading.db'
        self.running = False
        self.trading_thread = None
        
        # Enhanced signal generation parameters for better profitability
        self.min_confidence_threshold = 0.75  # Higher threshold for better signals
        self.volume_threshold = 1.5  # Minimum volume ratio for valid signals
        self.volatility_threshold = 0.02  # Maximum volatility for safe trades
        self.trend_strength_threshold = 0.6  # Minimum trend strength
        
        # Symbol mapping for comprehensive data fetching
        self.symbol_token_map = {
            'SBIN': '3045',
            'BANKNIFTY': '99992000', 
            'INFY': '11536',
            'RELIANCE': '2885',
            'TCS': '2951',
            'HDFCBANK': '1333',
            'ICICIBANK': '496',
            'BHARTIARTL': '319',
            'KOTAKBANK': '1922',
            'BAJFINANCE': '317',
            'HINDUNILVR': '1330',
            'NIFTY50': '26000',
            'SENSEX': '1',
            'FINNIFTY': '26037',
            'MIDCPNIFTY': '26017'
        }
        
    def start(self):
        """Start the auto trading system"""
        if self.running:
            logger.warning('Auto trading system is already running')
            return
            
        self.running = True
        self.trading_thread = threading.Thread(target=self._trading_loop)
        self.trading_thread.daemon = True
        self.trading_thread.start()
        logger.info('Auto trading system started')
        
    def stop(self):
        """Stop the auto trading system"""
        if not self.running:
            logger.warning('Auto trading system is not running')
            return
            
        self.running = False
        if self.trading_thread:
            self.trading_thread.join()
        logger.info('Auto trading system stopped')
    
    def _is_market_open(self) -> bool:
        """Check if market is currently open"""
        try:
            from datetime import timezone
            import pytz
            
            # Get current IST time
            ist = pytz.timezone('Asia/Kolkata')
            now = datetime.now(ist)
            
            # Check if it's a weekday (Monday to Friday)
            if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
                return False
                
            # Check market hours (9:15 AM to 3:30 PM IST)
            current_time = now.time()
            market_open = datetime.strptime('09:15', '%H:%M').time()
            market_close = datetime.strptime('15:30', '%H:%M').time()
            
            return market_open <= current_time <= market_close
            
        except Exception as e:
            logger.error(f"Error checking market hours: {str(e)}")
            return False
        
    def _trading_loop(self):
        """Main trading loop"""
        while self.running:
            try:
                # Check if market is open before processing trades
                if self._is_market_open():
                    # Process each active trade
                    self._process_active_trades()

                    # Automatically update positions (check exit conditions and adjust trailing stops)
                    self._auto_update_positions()

                    # Update trailing stops for active trades
                    self._update_trailing_stops()

                    # Check for new trading opportunities using enhanced method
                    # Assuming a default user_id for automated trades for now
                    # In a real application, this would come from user session or configuration
                    self._check_enhanced_trading_opportunities()
                else:
                    logger.info("Market is closed, skipping trading operations")
                
                # Sleep to prevent excessive CPU usage and API calls
                time.sleep(30)  # Check every 30 seconds to reduce CPU load
                
            except Exception as e:
                logger.error(f'Error in trading loop: {str(e)}')
                time.sleep(5)  # Sleep longer on error
                
    def _process_active_trades(self):
        """Process all active trades"""
        try:
            for trade_id, trade in list(self.active_trades.items()):
                # Get current price
                current_price = self.risk_manager.get_current_price(trade['symbol'])
                if not current_price:
                    continue
                    
                # Check if stop loss or take profit is hit
                if (trade['direction'] == 'BUY' and current_price['price'] <= trade['stop_loss']) or \
                   (trade['direction'] == 'SELL' and current_price['price'] >= trade['stop_loss']):
                    logger.info(f"Stop loss hit for {trade['symbol']} trade {trade_id}. Closing trade.")
                    self.close_trade(trade_id)

                elif (trade['direction'] == 'BUY' and current_price['price'] >= trade['take_profit']) or \
                     (trade['direction'] == 'SELL' and current_price['price'] <= trade['take_profit']):
                    logger.info(f"Take profit hit for {trade['symbol']} trade {trade_id}. Closing trade.")
                    self.close_trade(trade_id)
                    
        except Exception as e:
            logger.error(f'Error processing active trades: {str(e)}')
            
    def _auto_update_positions(self):
        """Automatically update positions based on market conditions"""
        try:
            for trade_id, trade in list(self.active_trades.items()):
                # Update trailing stops
                self._update_trailing_stop(trade_id, trade)
                
                # Check for additional exit conditions
                self._check_exit_conditions(trade_id, trade)
                
        except Exception as e:
            logger.error(f'Error updating positions: {str(e)}')
            
    def _update_trailing_stops(self):
        """Update trailing stops for all active trades"""
        try:
            for trade_id, trade in list(self.active_trades.items()):
                if 'trailing_stop' in trade:
                    self._update_trailing_stop(trade_id, trade)
                    
        except Exception as e:
            logger.error(f'Error updating trailing stops: {str(e)}')
            
    def _update_trailing_stop(self, trade_id: str, trade: Dict):
        """Update trailing stop for a specific trade"""
        try:
            if 'trailing_stop' not in trade:
                return
                
            current_price = self.risk_manager.get_current_price(trade['symbol'])
            if not current_price:
                return
                
            price = current_price['price']
            
            if trade['direction'] == 'BUY':
                # For long positions, trail the stop loss upward
                if price > trade['entry_price']:
                    new_stop = price - (trade['entry_price'] - trade['stop_loss'])
                    if new_stop > trade['stop_loss']:
                        trade['stop_loss'] = new_stop
                        logger.info(f"Updated trailing stop for {trade_id} to {new_stop}")
                        
            elif trade['direction'] == 'SELL':
                # For short positions, trail the stop loss downward
                if price < trade['entry_price']:
                    new_stop = price + (trade['stop_loss'] - trade['entry_price'])
                    if new_stop < trade['stop_loss']:
                        trade['stop_loss'] = new_stop
                        logger.info(f"Updated trailing stop for {trade_id} to {new_stop}")
                        
        except Exception as e:
            logger.error(f'Error updating trailing stop for {trade_id}: {str(e)}')
            
    def _check_exit_conditions(self, trade_id: str, trade: Dict):
        """Check for additional exit conditions beyond stop loss and take profit"""
        try:
            # Check for time-based exits
            if 'max_hold_time' in trade:
                hold_time = datetime.now() - trade['entry_time']
                if hold_time.total_seconds() > trade['max_hold_time']:
                    logger.info(f"Maximum hold time reached for {trade_id}. Closing trade.")
                    self.close_trade(trade_id)
                    return
                    
            # Check for volatility-based exits
            if 'max_volatility' in trade:
                volatility = self._calculate_volatility(trade['symbol'])
                if volatility > trade['max_volatility']:
                    logger.info(f"Volatility threshold exceeded for {trade_id}. Closing trade.")
                    self.close_trade(trade_id)
                    return
                    
        except Exception as e:
            logger.error(f'Error checking exit conditions for {trade_id}: {str(e)}')
            
    def _check_trading_opportunities(self, user_id: int):
        """Check for new trading opportunities"""
        try:
            # Get all available symbols (use working symbols to avoid errors)
            symbols = get_working_symbols()
            
            # Process only a few symbols at a time to avoid overwhelming the API
            symbols_to_process = symbols[:5]  # Process only first 5 symbols
            
            for symbol in symbols_to_process:
                try:
                    # Analyze market for this symbol
                    analysis = self.trading_system.analyze_market(symbol)
                    
                    if analysis['signal'] == 'BUY' and analysis['confidence'] > 0.7:
                        # Check if we can open a new position
                        if self._can_open_position(user_id, symbol):
                            self._open_automated_trade(user_id, symbol, 'BUY', analysis)
                            
                    elif analysis['signal'] == 'SELL' and analysis['confidence'] > 0.7:
                        # Check if we can open a new position
                        if self._can_open_position(user_id, symbol):
                            self._open_automated_trade(user_id, symbol, 'SELL', analysis)
                            
                    # Add small delay between symbols to avoid rate limiting
                    time.sleep(0.2)
                    
                except Exception as e:
                    logger.error(f'Error analyzing {symbol}: {str(e)}')
                    continue
                        
        except Exception as e:
            logger.error(f'Error checking trading opportunities: {str(e)}')
            
    def _can_open_position(self, user_id: int, symbol: str) -> bool:
        """Check if we can open a new position for this user and symbol"""
        try:
            # Check risk limits
            can_trade, message = self.risk_manager.check_risk_limits(user_id, symbol, 1.0)
            if not can_trade:
                logger.info(f"Cannot open position for {symbol}: {message}")
                return False
                
            # Check if we already have an active trade for this symbol
            for trade in self.active_trades.values():
                if trade['symbol'] == symbol and trade['user_id'] == user_id:
                    logger.info(f"Already have active trade for {symbol}")
                    return False
                    
            return True
            
        except Exception as e:
            logger.error(f'Error checking if can open position: {str(e)}')
            return False
            
    def _open_automated_trade(self, user_id: int, symbol: str, direction: str, analysis: Dict):
        """Open an automated trade"""
        try:
            # Calculate position size
            position_size = self.risk_manager.calculate_position_size(user_id, symbol)
            if not position_size:
                logger.warning(f"Could not calculate position size for {symbol}")
                return
                
            # Get current price
            current_price = self.risk_manager.get_current_price(symbol)
            if not current_price:
                logger.warning(f"Could not get current price for {symbol}")
                return
                
            # Calculate stop loss and take profit
            stop_loss, take_profit = self._calculate_stop_loss_take_profit(
                symbol, direction, current_price['price']
            )
            
            # Create trade record
            trade = {
                'user_id': user_id,
                'symbol': symbol,
                'direction': direction,
                'quantity': position_size,
                'entry_price': current_price['price'],
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'entry_time': datetime.now(),
                'trailing_stop': True,
                'max_hold_time': 24 * 60 * 60,  # 24 hours in seconds
                'max_volatility': 0.05,  # 5% volatility threshold
                'analysis': analysis
            }
            
            # Generate trade ID
            trade_id = f"auto_{symbol}_{int(time.time())}"
            
            # Add to active trades
            self.active_trades[trade_id] = trade
            
            # Log the trade
            logger.info(f"Opened automated {direction} trade for {symbol}: {trade_id}")
            
        except Exception as e:
            logger.error(f'Error opening automated trade: {str(e)}')
            
    def _calculate_stop_loss_take_profit(self, symbol: str, direction: str, entry_price: float) -> tuple:
        """Calculate stop loss and take profit levels"""
        try:
            # Get volatility for this symbol
            volatility = self._calculate_volatility(symbol)
            
            # Calculate stop loss and take profit based on volatility
            if direction == 'BUY':
                stop_loss = entry_price * (1 - volatility)
                take_profit = entry_price * (1 + volatility * 2)  # 2:1 risk-reward ratio
            else:  # SELL
                stop_loss = entry_price * (1 + volatility)
                take_profit = entry_price * (1 - volatility * 2)  # 2:1 risk-reward ratio
                
            return stop_loss, take_profit
            
        except Exception as e:
            logger.error(f'Error calculating stop loss and take profit: {str(e)}')
            # Return default values
            if direction == 'BUY':
                return entry_price * 0.98, entry_price * 1.04
            else:
                return entry_price * 1.02, entry_price * 0.96
                
    def _calculate_volatility(self, symbol: str) -> float:
        """Calculate volatility for a symbol"""
        try:
            # This is a simplified volatility calculation
            # In a real application, you would use historical price data
            return 0.02  # Default 2% volatility
            
        except Exception as e:
            logger.error(f'Error calculating volatility: {str(e)}')
            return 0.02
            
    def close_trade(self, trade_id: str):
        """Close a trade"""
        try:
            if trade_id in self.active_trades:
                trade = self.active_trades[trade_id]
                logger.info(f"Closing trade {trade_id} for {trade['symbol']}")
                
                # Remove from active trades
                del self.active_trades[trade_id]
                
                # Here you would implement the actual trade closing logic
                # This might involve calling the trading system to close the position
                
        except Exception as e:
            logger.error(f'Error closing trade {trade_id}: {str(e)}')
            
    def get_active_trades(self) -> Dict:
        """Get all active trades"""
        return self.active_trades.copy()
        
    def get_trade_status(self, trade_id: str) -> Optional[Dict]:
        """Get status of a specific trade"""
        return self.active_trades.get(trade_id)

    def _check_enhanced_trading_opportunities(self):
        """Check for trading opportunities using comprehensive market data"""
        try:
            if not angel_one_client or not angel_one_client.is_connected:
                logger.warning("Angel One not connected, skipping enhanced opportunity check")
                return
            
            # Get comprehensive market data for multiple symbols
            symbols_to_fetch = {
                "NSE": list(self.symbol_token_map.values())[:10]  # Fetch top 10 symbols
            }
            
            logger.info("Fetching comprehensive market data for enhanced opportunity analysis...")
            market_data = angel_one_client.fetch_market_data_direct(symbols_to_fetch)
            
            if not market_data or not market_data.get('status'):
                logger.warning("Failed to fetch comprehensive market data")
                return
            
            # Process each symbol's data
            for token_data in market_data['data']['fetched']:
                try:
                    symbol_token = token_data.get('symboltoken')
                    symbol_name = self._get_symbol_name_from_token(symbol_token)
                    
                    if not symbol_name:
                        continue
                    
                    # Generate enhanced signal
                    signal_analysis = angel_one_client.generate_enhanced_signal(token_data)
                    
                    if signal_analysis['signal'] in ['BUY', 'SELL'] and \
                       signal_analysis['confidence'] >= self.min_confidence_threshold:
                        
                        # Check if we can open a position
                        if self._can_open_position_enhanced(1, symbol_name, signal_analysis):
                            self._open_enhanced_trade(1, symbol_name, signal_analysis['signal'], signal_analysis)
                            
                except Exception as e:
                    logger.error(f'Error processing symbol {token_data.get("symboltoken")}: {str(e)}')
                    continue
                    
        except Exception as e:
            logger.error(f'Error checking enhanced trading opportunities: {str(e)}')

    def _can_open_position_enhanced(self, user_id: int, symbol: str, signal_analysis: Dict) -> bool:
        """Enhanced position opening checks"""
        try:
            # Basic risk checks
            can_trade, message = self.risk_manager.check_risk_limits(user_id, symbol, 1.0)
            if not can_trade:
                logger.info(f"Cannot open position for {symbol}: {message}")
                return False
            
            # Check if we already have an active trade for this symbol
            for trade in self.active_trades.values():
                if trade['symbol'] == symbol and trade['user_id'] == user_id:
                    logger.info(f"Already have active trade for {symbol}")
                    return False
            
            # Enhanced checks
            
            # 1. Volume check
            if signal_analysis['volume_score'] < 0.5:
                logger.info(f"Insufficient volume for {symbol}")
                return False
            
            # 2. Volatility check
            if signal_analysis['volatility_score'] > 0.03:  # 3% volatility threshold
                logger.info(f"Too volatile for {symbol}")
                return False
            
            # 3. Confidence check
            if signal_analysis['confidence'] < self.min_confidence_threshold:
                logger.info(f"Insufficient confidence for {symbol}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f'Error checking if can open enhanced position: {str(e)}')
            return False

    def _open_enhanced_trade(self, user_id: int, symbol: str, direction: str, signal_analysis: Dict):
        """Open an enhanced trade with comprehensive data"""
        try:
            # Calculate position size with enhanced risk management
            position_size = self.risk_manager.calculate_advanced_position_size(user_id, symbol)
            if not position_size:
                logger.warning(f"Could not calculate position size for {symbol}")
                return
            
            entry_price = signal_analysis['entry_price']
            
            # Calculate enhanced stop loss and take profit
            stop_loss, take_profit = self._calculate_enhanced_stop_loss_take_profit(
                symbol, direction, entry_price, signal_analysis
            )
            
            # Create enhanced trade record
            trade = {
                'user_id': user_id,
                'symbol': symbol,
                'direction': direction,
                'quantity': position_size,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'entry_time': datetime.now(),
                'trailing_stop': True,
                'max_hold_time': 4 * 60 * 60,  # 4 hours maximum hold
                'max_volatility': 0.03,  # 3% volatility threshold
                'signal_analysis': signal_analysis,
                'enhanced_trade': True
            }
            
            # Generate trade ID
            trade_id = f"enhanced_{symbol}_{int(time.time())}"
            
            # Add to active trades
            self.active_trades[trade_id] = trade
            
            # Log the enhanced trade
            logger.info(f"Opened enhanced {direction} trade for {symbol}: {trade_id}")
            logger.info(f"Entry: {entry_price}, SL: {stop_loss}, TP: {take_profit}")
            logger.info(f"Confidence: {signal_analysis['confidence']:.2f}, Volume Score: {signal_analysis['volume_score']:.2f}")
            
        except Exception as e:
            logger.error(f'Error opening enhanced trade: {str(e)}')

    def _calculate_enhanced_stop_loss_take_profit(self, symbol: str, direction: str, 
                                                entry_price: float, signal_analysis: Dict) -> tuple:
        """Calculate enhanced stop loss and take profit based on comprehensive data"""
        try:
            # Base risk percentage
            base_risk = 0.02  # 2%
            
            # Adjust risk based on volatility
            volatility_adjustment = 1.0 + signal_analysis['volatility_score'] * 2
            adjusted_risk = base_risk * volatility_adjustment
            
            # Adjust risk based on confidence
            confidence_adjustment = 1.0 + (signal_analysis['confidence'] - 0.75) * 0.5
            final_risk = adjusted_risk * confidence_adjustment
            
            # Ensure risk is within reasonable bounds
            final_risk = max(0.01, min(0.05, final_risk))  # 1% to 5%
            
            # Calculate stop loss and take profit
            if direction == 'BUY':
                stop_loss = entry_price * (1 - final_risk)
                take_profit = entry_price * (1 + final_risk * 2.5)  # 2.5:1 risk-reward
            else:  # SELL
                stop_loss = entry_price * (1 + final_risk)
                take_profit = entry_price * (1 - final_risk * 2.5)  # 2.5:1 risk-reward
                
            return stop_loss, take_profit
            
        except Exception as e:
            logger.error(f'Error calculating enhanced stop loss and take profit: {str(e)}')
            # Return default values
            if direction == 'BUY':
                return entry_price * 0.98, entry_price * 1.05
            else:
                return entry_price * 1.02, entry_price * 0.95

    def _get_symbol_name_from_token(self, token: str) -> Optional[str]:
        """Get symbol name from token"""
        for name, token_value in self.symbol_token_map.items():
            if token_value == token:
                return name
        return None

    def get_enhanced_trade_status(self) -> Dict:
        """Get enhanced trade status and performance metrics"""
        try:
            total_trades = len(self.active_trades)
            total_pnl = 0.0
            winning_trades = 0
            
            for trade in self.active_trades.values():
                # Calculate current P&L (simplified)
                if 'signal_analysis' in trade:
                    current_price = trade['signal_analysis']['entry_price']  # Simplified
                    if trade['direction'] == 'BUY':
                        pnl = (current_price - trade['entry_price']) * trade['quantity']
                    else:
                        pnl = (trade['entry_price'] - current_price) * trade['quantity']
                    
                    total_pnl += pnl
                    if pnl > 0:
                        winning_trades += 1
            
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            return {
                'status': 'success',
                'active_trades': total_trades,
                'total_pnl': total_pnl,
                'winning_trades': winning_trades,
                'win_rate': win_rate,
                'enhanced_trading_active': self.running,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f'Error getting enhanced trade status: {str(e)}')
            return {
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }