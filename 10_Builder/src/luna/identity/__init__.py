"""Identity management — face recognition + permission bridge + ambassador."""
from .bridge import AccessBridge, BridgeResult
from .permissions import filter_documents, get_denial_message, gate_content
from .ambassador import AmbassadorProxy, AmbassadorProtocol, AmbassadorResult
