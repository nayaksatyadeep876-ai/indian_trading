import os
from dotenv import load_dotenv
import mysql.connector
from mysql.connector import Error

# Load environment variables
load_dotenv()

def get_db_connection():
    """Create a connection to the MySQL database"""
    try:
        connection = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=os.getenv('MYSQL_PASSWORD', ''),
            database=os.getenv('MYSQL_DATABASE', 'kishanx_trading')
        )
        if connection.is_connected():
            print("Successfully connected to MySQL database")
            return connection
    except Error as e:
        print(f"Error connecting to MySQL database: {e}")
        return None

def init_database():
    """Initialize the database with required tables"""
    connection = get_db_connection()
    if connection is None:
        return

    try:
        cursor = connection.cursor()
        
        # Create users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                registered_at DATETIME NOT NULL,
                last_login DATETIME,
                balance DECIMAL(10,2) DEFAULT 10000.00,
                is_premium BOOLEAN DEFAULT 0,
                demo_end_time DATETIME
            )
        """)

        # Create signals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                pair VARCHAR(50) NOT NULL,
                direction VARCHAR(10) NOT NULL,
                confidence DECIMAL(5,2) NOT NULL,
                time DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                entry_price DECIMAL(10,2),
                stop_loss DECIMAL(10,2),
                take_profit DECIMAL(10,2),
                result INT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(50) NOT NULL,
                direction VARCHAR(10) NOT NULL,
                entry_price DECIMAL(10,2) NOT NULL,
                exit_price DECIMAL(10,2),
                quantity DECIMAL(10,2) NOT NULL,
                status VARCHAR(20) NOT NULL,
                entry_time DATETIME NOT NULL,
                exit_time DATETIME,
                profit_loss DECIMAL(10,2),
                stop_loss DECIMAL(10,2),
                take_profit DECIMAL(10,2),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create positions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(50) NOT NULL,
                quantity DECIMAL(10,2) NOT NULL,
                average_price DECIMAL(10,2) NOT NULL,
                last_updated DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, symbol)
            )
        """)

        # Create market_data table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_data (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(50) NOT NULL,
                price DECIMAL(10,2) NOT NULL,
                volume DECIMAL(20,2),
                timestamp DATETIME NOT NULL,
                UNIQUE(symbol, timestamp)
            )
        """)

        # Create risk_limits table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_limits (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                max_position_size DECIMAL(5,2) DEFAULT 0.02,
                max_daily_loss DECIMAL(5,2) DEFAULT 0.05,
                max_drawdown DECIMAL(5,2) DEFAULT 0.15,
                stop_loss_pct DECIMAL(5,2) DEFAULT 0.02,
                take_profit_pct DECIMAL(5,2) DEFAULT 0.04,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create portfolio_history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                portfolio_value DECIMAL(10,2) NOT NULL,
                timestamp DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Create user_subscriptions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                symbol VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, symbol)
            )
        """)

        connection.commit()
        print("Database tables created successfully")

    except Error as e:
        print(f"Error creating database tables: {e}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
            print("Database connection closed")

if __name__ == "__main__":
    init_database() 