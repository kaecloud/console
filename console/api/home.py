from flask import Blueprint


bp = Blueprint('home', __name__)


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


