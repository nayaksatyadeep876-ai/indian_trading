# 🔧 Troubleshooting Guide - Enhanced Auto-Trading System

## 🚨 Common Issues and Solutions

### 1. Angel One API Connection Issues

#### Problem: "Angel One API: Disconnected"
**Symptoms**:
- Dashboard shows "Disconnected" status
- No market data available
- Auto-trading not working

**Solutions**:
1. **Check API Credentials**:
   ```bash
   # Verify .env file contains correct credentials
   ANGEL_ONE_API_KEY=your_api_key
   ANGEL_ONE_CLIENT_ID=your_client_id
   ANGEL_ONE_PASSWORD=your_password
   ANGEL_ONE_TOTP_SECRET=your_totp_secret
   ```

2. **Verify TOTP Secret**:
   - Ensure TOTP secret is 26 characters long
   - Check if TOTP is generating valid codes
   - Verify time synchronization on your system

3. **Restart Flask Server**:
   ```bash
   # Stop all Python processes
   python restart_flask_server.py
   
   # Or manually restart
   python app.py
   ```

4. **Check Network Connection**:
   - Ensure internet connectivity
   - Verify firewall settings
   - Check proxy configurations

#### Problem: "Couldn't parse the JSON response received from the server: b''"
**Symptoms**:
- Market data fetch failures
- Empty responses from Angel One API

**Solutions**:
1. **Market Hours Check**:
   - This is normal when markets are closed (after 3:30 PM)
   - Wait for market hours (9:15 AM - 3:30 PM IST)

2. **API Rate Limiting**:
   - Reduce request frequency
   - Check if you're hitting rate limits
   - Wait and retry after some time

3. **Token Issues**:
   - Refresh Angel One session
   - Re-authenticate with new TOTP

### 2. Dashboard Access Issues

#### Problem: "Failed to fetch" errors on dashboard
**Symptoms**:
- Dashboard shows connection errors
- API calls failing
- No data loading

**Solutions**:
1. **Check Flask Server**:
   ```bash
   # Verify server is running
   netstat -an | findstr :5000
   
   # Start server if not running
   python app.py
   ```

2. **Authentication Issues**:
   - Login to the main interface first
   - Check session validity
   - Clear browser cache and cookies

3. **Use Test Endpoints**:
   - Access test dashboard: `http://localhost:8080`
   - Use test endpoints for debugging

#### Problem: "401 Unauthorized" errors
**Symptoms**:
- Authentication required errors
- Cannot access protected endpoints

**Solutions**:
1. **Login First**:
   - Go to `http://localhost:5000/login`
   - Enter your credentials
   - Access enhanced dashboard after login

2. **Session Issues**:
   - Clear browser cookies
   - Restart browser
   - Check session timeout

### 3. Auto-Trading Issues

#### Problem: Auto-trading not starting
**Symptoms**:
- "Start Auto-Trading" button not working
- No trading activity
- System shows "Stopped" status

**Solutions**:
1. **Check System Status**:
   ```bash
   # Test system status
   python test_angel_one_connection.py
   ```

2. **Verify Market Hours**:
   - Auto-trading only works during market hours
   - Check current time vs market hours (9:15 AM - 3:30 PM IST)

3. **Check Permissions**:
   - Ensure you have trading permissions
   - Verify account status with Angel One

4. **Restart Auto-Trader**:
   ```bash
   # Stop and start auto-trading
   python start_enhanced_trading.py
   ```

#### Problem: No trading signals generated
**Symptoms**:
- Dashboard shows "No signals available"
- System not generating buy/sell signals

**Solutions**:
1. **Market Conditions**:
   - Signals only generated during market hours
   - Check if market is open
   - Verify market data availability

2. **Signal Parameters**:
   - Check confidence thresholds
   - Verify risk-reward ratios
   - Review signal generation criteria

3. **Data Quality**:
   - Ensure market data is being fetched
   - Check data freshness and accuracy
   - Verify symbol tokens are correct

### 4. Performance Issues

#### Problem: Slow dashboard loading
**Symptoms**:
- Dashboard takes long to load
- Delayed data updates
- Poor responsiveness

**Solutions**:
1. **System Resources**:
   - Check CPU and memory usage
   - Close unnecessary applications
   - Restart system if needed

2. **Network Issues**:
   - Check internet speed
   - Verify local network stability
   - Test with different network

3. **Browser Issues**:
   - Clear browser cache
   - Disable browser extensions
   - Try different browser

#### Problem: High memory usage
**Symptoms**:
- System running slowly
- High memory consumption
- Potential crashes

**Solutions**:
1. **Restart Services**:
   ```bash
   # Restart Flask server
   python restart_flask_server.py
   ```

2. **Check Logs**:
   - Review system logs for errors
   - Look for memory leaks
   - Monitor resource usage

3. **Optimize Configuration**:
   - Reduce logging levels
   - Limit concurrent requests
   - Optimize data caching

### 5. Database Issues

#### Problem: "Cannot operate on a closed database"
**Symptoms**:
- Database connection errors
- Data not saving
- System instability

**Solutions**:
1. **Restart System**:
   ```bash
   # Complete system restart
   python restart_flask_server.py
   ```

2. **Check Database File**:
   - Verify database file exists
   - Check file permissions
   - Ensure no corruption

3. **Database Locks**:
   - Close all database connections
   - Restart application
   - Check for concurrent access

### 6. Market Data Issues

#### Problem: "No market data available"
**Symptoms**:
- Dashboard shows no data
- Symbols not found
- Empty market data responses

**Solutions**:
1. **Market Hours**:
   - This is normal when markets are closed
   - Wait for market hours (9:15 AM - 3:30 PM IST)

2. **Symbol Issues**:
   - Verify symbol names are correct
   - Check symbol tokens
   - Ensure symbols are tradeable

3. **API Issues**:
   - Check Angel One API status
   - Verify API credentials
   - Test with different symbols

## 🔍 Diagnostic Tools

### 1. System Status Check
```bash
# Check system status
python test_angel_one_connection.py

# Check market status
python check_market_status.py

# Diagnose connection issues
python diagnose_angel_one_connection.py
```

### 2. API Testing
```bash
# Test API endpoints
python test_api_direct.py

# Test enhanced system
python test_enhanced_dashboard_integration.py
```

### 3. Live Monitoring
```bash
# Monitor system in real-time
python live_monitor.py

# Check system processes
python check_system_status.py
```

## 📊 Error Codes and Meanings

### HTTP Status Codes
- **200**: Success
- **401**: Authentication required
- **403**: Access forbidden
- **404**: Endpoint not found
- **500**: Internal server error

### Angel One API Errors
- **Access denied**: Invalid credentials or permissions
- **Rate limit exceeded**: Too many requests
- **Market closed**: Trading outside market hours
- **Invalid symbol**: Symbol not found or not tradeable

### System Errors
- **Database connection failed**: Database issues
- **API connection failed**: Angel One API issues
- **Signal generation failed**: Market data or analysis issues
- **Trade execution failed**: Order placement issues

## 🚀 Performance Optimization

### 1. System Optimization
- **Regular Restarts**: Restart system daily
- **Memory Management**: Monitor memory usage
- **Log Rotation**: Manage log file sizes
- **Cache Management**: Clear caches regularly

### 2. Network Optimization
- **Stable Connection**: Use reliable internet
- **Proxy Settings**: Configure if needed
- **Firewall Rules**: Allow necessary ports
- **DNS Settings**: Use reliable DNS servers

### 3. Browser Optimization
- **Clear Cache**: Regular cache clearing
- **Disable Extensions**: Remove unnecessary extensions
- **Update Browser**: Keep browser updated
- **Hardware Acceleration**: Enable if available

## 📞 Support and Escalation

### 1. Self-Help Steps
1. Check this troubleshooting guide
2. Review system logs
3. Test with diagnostic tools
4. Restart system components

### 2. Common Solutions
1. **Restart Flask Server**: `python restart_flask_server.py`
2. **Check Market Hours**: Verify market is open
3. **Verify Credentials**: Check API credentials
4. **Clear Browser Cache**: Refresh browser data

### 3. When to Escalate
- Persistent connection issues
- Data corruption problems
- System crashes
- Security concerns

## 📝 Log Analysis

### 1. Important Log Messages
- **"Successfully connected to Angel One API"**: Connection successful
- **"Market is closed"**: Normal during off-market hours
- **"Failed to fetch data"**: API or network issues
- **"Auto trading started"**: System operational

### 2. Error Patterns
- **Repeated connection failures**: Network or credential issues
- **Frequent API errors**: Rate limiting or API issues
- **Database errors**: Database connection problems
- **Memory warnings**: Resource management issues

### 3. Log Locations
- **Flask Logs**: Console output
- **System Logs**: Application logs
- **Error Logs**: Error-specific logs
- **Debug Logs**: Detailed debugging information

---

**🔧 Comprehensive Troubleshooting Guide for Enhanced Auto-Trading System**

*Last Updated: September 6, 2025*
*Version: 1.0.0*
