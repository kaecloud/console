from console.libs.view import create_api_blueprint, user_require
from console.libs.utils import get_cluster_names

bp = create_api_blueprint('cluster', __name__, 'cluster')


@bp.route('/')
@user_require(True)
def list_cluster():
    """
    List all the available clusters
    ---
    responses:
      200:
        description: available cluster list
        schema:
          type: array
          items:
            type: string
        examples:
          application/json: [
            "cluster1",
            "cluster2",
            ]
    """
    return get_cluster_names()
