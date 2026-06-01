"""① 风险分级 (机制)。规则来自领域包;误判向上不向下。"""
from marshal_core.contracts import NormalizedEvent
from marshal_core.domain_pack import DomainPack


class Classifier:
    def __init__(self, pack: DomainPack):
        self.pack = pack

    def tier(self, event: NormalizedEvent) -> str:
        return self.pack.classify({"repo": event.repo, "diff_paths": event.diff_paths})
