from flask import Blueprint, render_template
from .ranking import load_players, PLAYERS

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    load_players()
    return render_template('index.html', players=PLAYERS)