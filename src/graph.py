# src/graph.py
# https://github.com/SubhanshuMG/oncall-burnout-fix
import networkx as nx
from typing import Optional
import json
import structlog

log = structlog.get_logger()


class ServiceDependencyGraph:
    """
    Represents the dependency relationships between microservices.
    When service A calls service B, an edge exists from A -> B.
    This lets us traverse upstream from a symptomatic service
    to find where the root cause likely lives.

    In production, call load_from_file() pointing at your service catalog
    (e.g. exported from Backstage) instead of using _build_default_graph().
    """

    def __init__(self):
        self.graph = nx.DiGraph()
        self._build_default_graph()

    def _build_default_graph(self):
        """
        Default topology for the payment platform used in the article.
        Replace with load_from_file() for your own service graph.
        """
        services = [
            "payment-service",
            "checkout-service",
            "order-service",
            "inventory-service",
            "notification-service",
            "postgres-rds",
            "redis-cache",
            "kafka",
        ]

        # Directed edges: caller -> dependency
        dependencies = [
            ("payment-service", "order-service"),
            ("payment-service", "redis-cache"),
            ("checkout-service", "payment-service"),
            ("checkout-service", "inventory-service"),
            ("checkout-service", "order-service"),
            ("order-service", "postgres-rds"),
            ("order-service", "kafka"),
            ("inventory-service", "postgres-rds"),
            ("notification-service", "kafka"),
        ]

        for service in services:
            self.graph.add_node(service)

        for caller, dependency in dependencies:
            self.graph.add_edge(caller, dependency)

        log.info("dependency_graph_built", nodes=len(services), edges=len(dependencies))

    def get_upstream_services(self, service: str) -> list[str]:
        """Services that depend ON this service (will be affected if it fails)"""
        if service not in self.graph:
            return []
        return list(self.graph.predecessors(service))

    def get_downstream_services(self, service: str) -> list[str]:
        """Services this service depends on (could be the root cause)"""
        if service not in self.graph:
            return []
        return list(self.graph.successors(service))

    def find_likely_root_cause(self, affected_services: list[str]) -> Optional[str]:
        """
        Given a list of affected services, find the node that
        has the most dependents in the affected set. That node
        is the most likely root cause of the cascade.
        """
        if not affected_services:
            return None

        affected_set = set(affected_services)
        scores = {}

        for service in affected_services:
            if service not in self.graph:
                continue
            upstream = set(self.graph.predecessors(service))
            scores[service] = len(upstream.intersection(affected_set))

        if not scores:
            return affected_services[0]

        return max(scores, key=scores.get)

    def get_impact_radius(self, root_service: str) -> list[str]:
        """All services that could be affected if root_service fails"""
        if root_service not in self.graph:
            return []
        impacted = set()
        queue = [root_service]
        while queue:
            current = queue.pop(0)
            predecessors = list(self.graph.predecessors(current))
            for pred in predecessors:
                if pred not in impacted:
                    impacted.add(pred)
                    queue.append(pred)
        return list(impacted)

    def load_from_file(self, path: str):
        """
        Load graph from a JSON service catalog file.

        Format:
        {
          "nodes": ["service-a", "service-b"],
          "edges": [{"from": "service-a", "to": "service-b"}]
        }
        """
        with open(path) as f:
            data = json.load(f)
        self.graph.clear()
        for node in data.get("nodes", []):
            self.graph.add_node(node)
        for edge in data.get("edges", []):
            self.graph.add_edge(edge["from"], edge["to"])
        log.info(
            "dependency_graph_loaded_from_file",
            path=path,
            nodes=self.graph.number_of_nodes(),
            edges=self.graph.number_of_edges(),
        )
