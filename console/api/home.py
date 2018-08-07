from flask import Blueprint, render_template
from console.libs.view import user_require


bp = Blueprint('home', __name__)


@bp.route('/')
@user_require(False, redirect_on_false=True)
def index():
    return render_template("index.html")


@bp.route('/healthz')
def healthz():
    """
    health status
    ---
    security: []
    responses:
      200:
        description: Health status
        examples:
          text/plain:
            "ok"
    """
    return 'ok'


