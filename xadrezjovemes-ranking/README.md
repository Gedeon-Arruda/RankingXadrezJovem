# xadrezjovemes-ranking

## Overview
This project is a Flask web application that provides a ranking system for chess players. It fetches player data from the Lichess API and displays it in a user-friendly interface.

## Project Structure
```
xadrezjovemes-ranking
├── src
│   ├── __init__.py
│   ├── ranking.py
│   ├── routes.py
│   ├── templates
│   │   └── index.html
│   └── static
│       ├── css
│       │   └── style.css
│       └── js
│           └── app.js
├── docs
│   └── index.html
├── requirements.txt
├── freeze.py
├── .github
│   └── workflows
│       └── deploy.yml
└── README.md
```

## Installation
To set up the project, follow these steps:

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/xadrezjovemes-ranking.git
   cd xadrezjovemes-ranking
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage
To run the application locally, execute the following command:
```
python src/ranking.py
```
Then, open your web browser and navigate to `http://127.0.0.1:8000`.

## Deployment
This project is configured to be deployed to GitHub Pages using GitHub Actions. The deployment workflow is defined in `.github/workflows/deploy.yml`.

## Contributing
Contributions are welcome! Please submit a pull request or open an issue for any suggestions or improvements.

## License
This project is licensed under the MIT License. See the LICENSE file for details.