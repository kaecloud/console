from console.app import oidc
from console.libs.view import create_api_blueprint
from console.libs.k8s import KubeApi

bp = create_api_blueprint('cluster', __name__, 'cluster')


@bp.route('/')
@oidc.accept_token(True)
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
    return KubeApi.instance().cluster_names
