from flask import current_app, request, stream_with_context, Blueprint, render_template
from console.libs.view import user_require
from console.libs import sse


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


@bp.route('/stream')
@user_require(False)
def stream():
    """
    A view function that streams server-sent events. Ignores any
    :mailheader:`Last-Event-ID` headers in the HTTP request.
    Use a "channel" query parameter to stream events from a different
    channel than the default channel (which is "sse").
    """
    channel = request.args.get('channel') or 'sse'

    @stream_with_context
    def generator():
        for message in sse.messages(channel=channel):
            yield str(message)

    return current_app.response_class(
        generator(),
        mimetype='text/event-stream',
    )


@bp.route('/test-sse')
def test_sse():
    @stream_with_context
    def generator():
        import time
        # for i in range(10):
        i = 0
        while True:
            time.sleep(1)
            msg = sse.Message({"data": "haha {}".format(i)})
            print(i)
            yield str(msg)
            i += 1
        # yield str(sse.Message({"data": "finished"}, type='close'))
        # yield str(sse.Message({"data": "finished2"}, type='close'))

    return current_app.response_class(
        generator(),
        mimetype='text/event-stream',
    )
