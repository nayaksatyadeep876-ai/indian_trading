# 📚 API Reference - Enhanced Auto-Trading System

## 🔗 Base URL
```
http://localhost:5000
```

## 🔐 Authentication
Most endpoints require user authentication via Flask session. Test endpoints are available for development.

## 📊 Enhanced Trading Endpoints

### 1. Enhanced Trading Signals
```http
GET /api/indian/enhanced_signals
```
**Description**: Get high-quality trading signals with comprehensive analysis

**Response**:
```json
{
  "status": "success",
  "signals": [
    {
      "symbol": "SBIN",
      "action": "BUY",
      "confidence": 85,
      "price": 806.6,
      "stop_loss": 780.0,
      "take_profit": 850.0,
      "risk_reward": 2.5,
      "analysis": {
        "ohlc_score": 8.5,
        "volume_score": 7.2,
        "trend_score": 9.1,
        "volatility_score": 6.8
      }
    }
  ],
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 2. Comprehensive Market Data
```http
GET /api/indian/market_data_bulk?symbols=SBIN,BANKNIFTY,INFY,RELIANCE
```
**Description**: Fetch comprehensive market data for multiple symbols

**Parameters**:
- `symbols` (string): Comma-separated list of symbols

**Response**:
```json
{
  "status": "success",
  "data": {
    "fetched": [
      {
        "symboltoken": "3045",
        "tradingSymbol": "SBIN-EQ",
        "exchange": "NSE",
        "ltp": 806.6,
        "netChange": -2.8,
        "percentChange": -0.35,
        "open": 811.5,
        "high": 812.5,
        "low": 803.65,
        "close": 809.4,
        "tradeVolume": 5194842,
        "52WeekHigh": 875.45,
        "52WeekLow": 680.0
      }
    ],
    "unfetched": []
  }
}
```

### 3. Enhanced Auto-Trade Status
```http
GET /api/indian/enhanced_auto_trade_status
```
**Description**: Get comprehensive system status and performance metrics

**Response**:
```json
{
  "status": "success",
  "angel_one_connected": true,
  "enhanced_trading_active": true,
  "active_trades": 0,
  "total_pnl": 0.0,
  "win_rate": 0.0,
  "total_trades": 0,
  "winning_trades": 0,
  "losing_trades": 0,
  "average_profit": 0.0,
  "max_drawdown": 0.0,
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 4. Start Auto-Trading
```http
POST /api/indian/start_auto_trading
```
**Description**: Start the enhanced auto-trading system

**Response**:
```json
{
  "status": "success",
  "message": "Enhanced auto-trading started successfully",
  "auto_trading_active": true,
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 5. Stop Auto-Trading
```http
POST /api/indian/stop_auto_trading
```
**Description**: Stop the enhanced auto-trading system

**Response**:
```json
{
  "status": "success",
  "message": "Enhanced auto-trading stopped successfully",
  "auto_trading_active": false,
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 6. Active Trades
```http
GET /api/indian/active_trades
```
**Description**: Get list of currently active trades

**Response**:
```json
{
  "status": "success",
  "trades": [
    {
      "trade_id": "T001",
      "symbol": "SBIN",
      "action": "BUY",
      "quantity": 100,
      "entry_price": 806.6,
      "current_price": 810.2,
      "pnl": 360.0,
      "pnl_percentage": 0.45,
      "stop_loss": 780.0,
      "take_profit": 850.0,
      "timestamp": "2025-09-06T21:00:00Z"
    }
  ]
}
```

## 🧪 Test Endpoints (No Authentication Required)

### 1. Test System Status
```http
POST /test_system_status
```
**Description**: Test system status without authentication

**Response**:
```json
{
  "status": "success",
  "angel_one_connected": true,
  "enhanced_trading_active": true,
  "active_trades": 0,
  "total_pnl": 0.0,
  "win_rate": 0.0,
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 2. Test Enhanced Signals
```http
GET /test_enhanced_signals
```
**Description**: Test signal generation without authentication

### 3. Test Market Data
```http
GET /test_market_data
```
**Description**: Test market data fetching without authentication

### 4. Test Start Auto-Trading
```http
POST /test_start_auto_trading
```
**Description**: Test starting auto-trading without authentication

### 5. Test Stop Auto-Trading
```http
POST /test_stop_auto_trading
```
**Description**: Test stopping auto-trading without authentication

### 6. Test Direct API
```http
GET /test_direct_api
```
**Description**: Test direct API connection without authentication

## 🔧 Core System Endpoints

### 1. Angel One Status
```http
GET /api/angel_one/status
```
**Description**: Get Angel One API connection status

**Response**:
```json
{
  "connected": true,
  "user": "N DIPTIBAN",
  "exchanges": ["nse_fo", "nse_cm", "cde_fo", "ncx_fo", "bse_fo", "bse_cm", "mcx_fo"],
  "timestamp": "2025-09-06T21:00:00Z"
}
```

### 2. Indian Market Status
```http
GET /indian_status
```
**Description**: Get Indian market trading status

**Response**:
```json
{
  "status": "success",
  "market_open": false,
  "total_pnl": 0.0,
  "active_trades": 0,
  "timestamp": "2025-09-06T21:00:00Z"
}
```

## 📱 Web Interface Endpoints

### 1. Enhanced Dashboard
```http
GET /enhanced_dashboard
```
**Description**: Access the enhanced trading dashboard

### 2. Indian Market Page
```http
GET /indian
```
**Description**: Access the main Indian market trading page

### 3. Login
```http
GET /login
POST /login
```
**Description**: User authentication

### 4. Logout
```http
GET /logout
```
**Description**: User logout

## 🚨 Error Responses

### Authentication Error
```json
{
  "error": "Not authenticated"
}
```
**Status Code**: 401

### Server Error
```json
{
  "error": "Internal server error"
}
```
**Status Code**: 500

### Bad Request
```json
{
  "error": "Invalid parameters"
}
```
**Status Code**: 400

## 📊 Rate Limiting

### Angel One API Limits
- **Market Data**: 10 requests/second, 500/minute, 5000/hour
- **Order Placement**: 20 requests/second, 500/minute, 1000/hour
- **Position Checks**: 1 request/second
- **Historical Data**: 3 requests/second, 180/minute, 5000/hour

### System Usage
- **Current Usage**: <2% of available quotas
- **Optimization**: Efficient batching and caching
- **Monitoring**: Real-time rate limit tracking

## 🔄 WebSocket Endpoints (Future)

### Real-time Updates
```javascript
// Future implementation
const ws = new WebSocket('ws://localhost:5000/ws');
ws.onmessage = function(event) {
  const data = JSON.parse(event.data);
  // Handle real-time updates
};
```

## 📝 Example Usage

### Python Example
```python
import requests

# Test system status
response = requests.post('http://localhost:5000/test_system_status')
data = response.json()
print(f"Angel One Connected: {data['angel_one_connected']}")

# Get enhanced signals
response = requests.get('http://localhost:5000/api/indian/enhanced_signals')
signals = response.json()
print(f"Signals: {signals['signals']}")
```

### JavaScript Example
```javascript
// Fetch system status
fetch('/test_system_status', { method: 'POST' })
  .then(response => response.json())
  .then(data => {
    console.log('System Status:', data);
  });

// Start auto-trading
fetch('/api/indian/start_auto_trading', { method: 'POST' })
  .then(response => response.json())
  .then(data => {
    console.log('Auto-trading started:', data);
  });
```

## 🔧 Configuration

### Environment Variables
```bash
ANGEL_ONE_API_KEY=your_api_key
ANGEL_ONE_CLIENT_ID=your_client_id
ANGEL_ONE_PASSWORD=your_password
ANGEL_ONE_TOTP_SECRET=your_totp_secret
```

### System Configuration
- **Trading Hours**: 9:15 AM - 3:30 PM (IST)
- **Signal Interval**: 15 seconds
- **Risk-Reward Ratio**: 2.5:1 minimum
- **Confidence Threshold**: 75% minimum

---

**📚 Complete API Reference for Enhanced Auto-Trading System**

*Last Updated: September 6, 2025*
*Version: 1.0.0*
