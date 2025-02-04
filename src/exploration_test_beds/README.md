# Robinhood Portfolio Analysis

This project analyzes a Robinhood portfolio's performance compared to major indices like the S&P 500 and popular ETFs such as ARKK. It calculates various performance metrics including Sharpe ratio, alpha, and maximum drawdown for each year from 2020 to the end of 2024, as well as cumulatively for the same period.

## Project Structure

```
robinhood-portfolio-analysis
├── src
│   ├── __init__.py
│   ├── main.py
│   ├── data
│   │   ├── __init__.py
│   │   └── data_loader.py
│   ├── analysis
│   │   ├── __init__.py
│   │   ├── performance_metrics.py
│   │   └── comparison.py
│   ├── utils
│       ├── __init__.py
│       └── helpers.py
├── requirements.txt
├── .gitignore
└── README.md
```

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/robinhood-portfolio-analysis.git
   ```
2. Navigate to the project directory:
   ```
   cd robinhood-portfolio-analysis
   ```
3. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

To run the analysis, execute the following command:
```
python src/main.py
```

## Modules

- **Data Loading**: The `data_loader.py` module contains the `DataLoader` class, which is responsible for loading the Robinhood portfolio data and major indices data.

- **Performance Metrics**: The `performance_metrics.py` module exports the `PerformanceMetrics` class, which includes methods to calculate the Sharpe ratio, alpha, and maximum drawdown.

- **Comparison**: The `comparison.py` module exports the `Comparison` class, which provides methods to compare the portfolio's performance against specified indices and ETFs.

- **Utilities**: The `helpers.py` module contains utility functions for data manipulation and calculations.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any enhancements or bug fixes.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.