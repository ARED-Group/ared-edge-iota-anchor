"""
ARED Edge IOTA Anchor Service - Merkle Tree Implementation

Provides deterministic Merkle tree construction with SHA-256 hashing,
inclusion proof generation, and verification.

The implementation follows RFC 6962 (Certificate Transparency) conventions:
- Leaf nodes are hashed with a 0x00 prefix
- Internal nodes are hashed with a 0x01 prefix
- This prevents second preimage attacks

For odd numbers of leaves, the last leaf is promoted (not duplicated)
to maintain a balanced tree structure.
"""

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProofDirection(str, Enum):
    """Direction indicator for proof path elements."""

    LEFT = "L"
    RIGHT = "R"


@dataclass(frozen=True)
class MerkleNode:
    """
    Represents a node in the Merkle tree.

    Attributes:
        hash: SHA-256 hash of the node
        left: Left child node (None for leaves)
        right: Right child node (None for leaves)
        data: Original data (only for leaf nodes)
        position: Position in the original leaf array
    """

    hash: str
    left: "MerkleNode | None" = None
    right: "MerkleNode | None" = None
    data: bytes | None = None
    position: int | None = None

    @property
    def is_leaf(self) -> bool:
        """Check if this node is a leaf."""
        return self.left is None and self.right is None


@dataclass
class ProofElement:
    """
    Single element in a Merkle proof path.

    Attributes:
        hash: The sibling hash at this level
        direction: Whether sibling is LEFT or RIGHT of the path
    """

    hash: str
    direction: ProofDirection

    def to_dict(self) -> dict[str, str]:
        """Serialize to dictionary."""
        return {"hash": self.hash, "direction": self.direction.value}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "ProofElement":
        """Deserialize from dictionary."""
        return cls(
            hash=data["hash"],
            direction=ProofDirection(data["direction"]),
        )


@dataclass
class MerkleProof:
    """
    Merkle inclusion proof for a leaf.

    Contains the path from leaf to root with sibling hashes.

    Attributes:
        leaf_hash: Hash of the leaf being proven
        leaf_index: Original index of the leaf
        proof_path: List of sibling hashes with directions
        root_hash: Expected Merkle root
        tree_size: Total number of leaves in the tree
    """

    leaf_hash: str
    leaf_index: int
    proof_path: list[ProofElement]
    root_hash: str
    tree_size: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize proof to dictionary for storage."""
        return {
            "leaf_hash": self.leaf_hash,
            "leaf_index": self.leaf_index,
            "proof_path": [e.to_dict() for e in self.proof_path],
            "root_hash": self.root_hash,
            "tree_size": self.tree_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MerkleProof":
        """Deserialize proof from dictionary."""
        return cls(
            leaf_hash=data["leaf_hash"],
            leaf_index=data["leaf_index"],
            proof_path=[ProofElement.from_dict(e) for e in data["proof_path"]],
            root_hash=data["root_hash"],
            tree_size=data["tree_size"],
        )

    def to_compact(self) -> list[str]:
        """
        Serialize to compact format (just the hashes with direction encoding).

        Format: ["L:hash1", "R:hash2", ...]
        """
        return [f"{e.direction.value}:{e.hash}" for e in self.proof_path]

    @classmethod
    def from_compact(
        cls,
        leaf_hash: str,
        leaf_index: int,
        compact_path: list[str],
        root_hash: str,
        tree_size: int,
    ) -> "MerkleProof":
        """Create proof from compact format."""
        proof_path = []
        for item in compact_path:
            direction, hash_value = item.split(":", 1)
            proof_path.append(
                ProofElement(
                    hash=hash_value,
                    direction=ProofDirection(direction),
                )
            )
        return cls(
            leaf_hash=leaf_hash,
            leaf_index=leaf_index,
            proof_path=proof_path,
            root_hash=root_hash,
            tree_size=tree_size,
        )


# Prefix bytes for domain separation (RFC 6962)
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def compute_leaf_hash(data: bytes | str) -> str:
    """
    Compute the hash of a leaf node.

    Uses domain separation with 0x00 prefix to prevent
    second preimage attacks.

    Args:
        data: Leaf data (bytes or hex string)

    Returns:
        Hex-encoded SHA-256 hash
    """
    if isinstance(data, str):
        # Assume hex-encoded string
        data = bytes.fromhex(data) if all(c in "0123456789abcdefABCDEF" for c in data) else data.encode("utf-8")

    hasher = hashlib.sha256()
    hasher.update(LEAF_PREFIX)
    hasher.update(data)
    return hasher.hexdigest()


def compute_parent_hash(left_hash: str, right_hash: str) -> str:
    """
    Compute the hash of an internal node.

    Uses domain separation with 0x01 prefix.

    Args:
        left_hash: Hash of left child (hex string)
        right_hash: Hash of right child (hex string)

    Returns:
        Hex-encoded SHA-256 hash
    """
    hasher = hashlib.sha256()
    hasher.update(NODE_PREFIX)
    hasher.update(bytes.fromhex(left_hash))
    hasher.update(bytes.fromhex(right_hash))
    return hasher.hexdigest()


class MerkleTree:
    """
    Merkle tree implementation with SHA-256 hashing.

    Features:
    - Deterministic construction from ordered leaves
    - Domain separation for leaf and internal nodes
    - Efficient proof generation
    - Support for odd number of leaves
    - Immutable after construction

    Example:
        >>> tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        >>> root = tree.root_hash
        >>> proof = tree.get_proof(0)
        >>> verify_proof(proof)
        True
    """

    def __init__(self, root: MerkleNode, leaves: list[MerkleNode]) -> None:
        """
        Initialize Merkle tree (internal use).

        Use from_leaves() or from_hashes() to construct trees.
        """
        self._root = root
        self._leaves = leaves

    @classmethod
    def from_leaves(cls, leaves: list[bytes]) -> "MerkleTree":
        """
        Construct a Merkle tree from leaf data.

        Args:
            leaves: List of leaf data (bytes)

        Returns:
            Constructed MerkleTree

        Raises:
            ValueError: If leaves is empty
        """
        if not leaves:
            raise ValueError("Cannot create Merkle tree from empty leaves")

        # Create leaf nodes
        leaf_nodes = []
        for i, data in enumerate(leaves):
            leaf_hash = compute_leaf_hash(data)
            leaf_nodes.append(
                MerkleNode(
                    hash=leaf_hash,
                    data=data,
                    position=i,
                )
            )

        return cls._build_tree(leaf_nodes)

    @classmethod
    def from_hashes(cls, hashes: list[str]) -> "MerkleTree":
        """
        Construct a Merkle tree from pre-computed leaf hashes.

        Useful when leaves are already hashed (e.g., event hashes).

        Args:
            hashes: List of hex-encoded leaf hashes

        Returns:
            Constructed MerkleTree

        Raises:
            ValueError: If hashes is empty
        """
        if not hashes:
            raise ValueError("Cannot create Merkle tree from empty hashes")

        # Create leaf nodes from pre-hashed data
        leaf_nodes = []
        for i, hash_value in enumerate(hashes):
            # For pre-hashed data, we hash again with leaf prefix
            # to maintain consistent tree structure
            leaf_hash = compute_leaf_hash(bytes.fromhex(hash_value))
            leaf_nodes.append(
                MerkleNode(
                    hash=leaf_hash,
                    data=bytes.fromhex(hash_value),
                    position=i,
                )
            )

        return cls._build_tree(leaf_nodes)

    @classmethod
    def from_raw_hashes(cls, hashes: list[str]) -> "MerkleTree":
        """
        Construct a Merkle tree using hashes directly as leaf hashes.

        Use when hashes are already properly formatted (e.g., from indexer).

        Args:
            hashes: List of hex-encoded hashes to use directly as leaves

        Returns:
            Constructed MerkleTree
        """
        if not hashes:
            raise ValueError("Cannot create Merkle tree from empty hashes")

        leaf_nodes = []
        for i, hash_value in enumerate(hashes):
            leaf_nodes.append(
                MerkleNode(
                    hash=hash_value,
                    data=bytes.fromhex(hash_value),
                    position=i,
                )
            )

        return cls._build_tree(leaf_nodes)

    @classmethod
    def _build_tree(cls, leaf_nodes: list[MerkleNode]) -> "MerkleTree":
        """Build tree from leaf nodes."""
        if len(leaf_nodes) == 1:
            return cls(leaf_nodes[0], leaf_nodes)

        # Build tree bottom-up
        current_level = leaf_nodes

        while len(current_level) > 1:
            next_level = []

            i = 0
            while i < len(current_level):
                left = current_level[i]

                if i + 1 < len(current_level):
                    # Normal case: pair left and right
                    right = current_level[i + 1]
                    parent_hash = compute_parent_hash(left.hash, right.hash)
                    parent = MerkleNode(
                        hash=parent_hash,
                        left=left,
                        right=right,
                    )
                    next_level.append(parent)
                    i += 2
                else:
                    # Odd case: promote the last node
                    next_level.append(left)
                    i += 1

            current_level = next_level

        return cls(current_level[0], leaf_nodes)

    @property
    def root(self) -> MerkleNode:
        """Get the root node."""
        return self._root

    @property
    def root_hash(self) -> str:
        """Get the root hash (Merkle root)."""
        return self._root.hash

    @property
    def leaves(self) -> list[MerkleNode]:
        """Get all leaf nodes."""
        return self._leaves

    @property
    def leaf_count(self) -> int:
        """Get the number of leaves."""
        return len(self._leaves)

    def get_leaf_hash(self, index: int) -> str:
        """
        Get the hash of a leaf by index.

        Args:
            index: Leaf index (0-based)

        Returns:
            Hex-encoded leaf hash

        Raises:
            IndexError: If index out of bounds
        """
        if index < 0 or index >= len(self._leaves):
            raise IndexError(f"Leaf index {index} out of bounds")
        return self._leaves[index].hash

    def get_proof(self, leaf_index: int) -> MerkleProof:
        """
        Generate inclusion proof for a leaf.

        Args:
            leaf_index: Index of the leaf to prove

        Returns:
            MerkleProof for the leaf

        Raises:
            IndexError: If leaf_index out of bounds
        """
        if leaf_index < 0 or leaf_index >= len(self._leaves):
            raise IndexError(f"Leaf index {leaf_index} out of bounds")

        proof_path = []
        current_index = leaf_index
        current_level = self._leaves

        while len(current_level) > 1:
            next_level = []
            i = 0

            while i < len(current_level):
                if i + 1 < len(current_level):
                    left = current_level[i]
                    right = current_level[i + 1]

                    # Check if current node is in this pair
                    if i == current_index:
                        # Current is left, sibling is right
                        proof_path.append(
                            ProofElement(
                                hash=right.hash,
                                direction=ProofDirection.RIGHT,
                            )
                        )
                        current_index = len(next_level)
                    elif i + 1 == current_index:
                        # Current is right, sibling is left
                        proof_path.append(
                            ProofElement(
                                hash=left.hash,
                                direction=ProofDirection.LEFT,
                            )
                        )
                        current_index = len(next_level)

                    parent_hash = compute_parent_hash(left.hash, right.hash)
                    next_level.append(MerkleNode(hash=parent_hash))
                    i += 2
                else:
                    # Odd element - promoted
                    if i == current_index:
                        current_index = len(next_level)
                    next_level.append(current_level[i])
                    i += 1

            current_level = next_level

        return MerkleProof(
            leaf_hash=self._leaves[leaf_index].hash,
            leaf_index=leaf_index,
            proof_path=proof_path,
            root_hash=self.root_hash,
            tree_size=len(self._leaves),
        )

    def get_all_proofs(self) -> list[MerkleProof]:
        """
        Generate proofs for all leaves.

        Returns:
            List of MerkleProof for each leaf
        """
        return [self.get_proof(i) for i in range(len(self._leaves))]


def verify_proof(proof: MerkleProof) -> bool:
    """
    Verify a Merkle inclusion proof.

    Reconstructs the root hash from the leaf hash and proof path,
    then compares with the expected root.

    Args:
        proof: MerkleProof to verify

    Returns:
        True if proof is valid
    """
    current_hash = proof.leaf_hash

    for element in proof.proof_path:
        if element.direction == ProofDirection.LEFT:
            # Sibling is on the left
            current_hash = compute_parent_hash(element.hash, current_hash)
        else:
            # Sibling is on the right
            current_hash = compute_parent_hash(current_hash, element.hash)

    return current_hash == proof.root_hash


def verify_proof_against_root(
    leaf_hash: str,
    proof_path: list[ProofElement],
    expected_root: str,
) -> bool:
    """
    Verify a proof against a specific root hash.

    Args:
        leaf_hash: Hash of the leaf
        proof_path: List of proof elements
        expected_root: Expected Merkle root

    Returns:
        True if proof reconstructs to expected root
    """
    current_hash = leaf_hash

    for element in proof_path:
        if element.direction == ProofDirection.LEFT:
            current_hash = compute_parent_hash(element.hash, current_hash)
        else:
            current_hash = compute_parent_hash(current_hash, element.hash)

    return current_hash == expected_root


def compute_root_from_proof(leaf_hash: str, proof_path: list[ProofElement]) -> str:
    """
    Compute the root hash from a leaf and proof path.

    Args:
        leaf_hash: Hash of the leaf
        proof_path: List of proof elements

    Returns:
        Computed root hash
    """
    current_hash = leaf_hash

    for element in proof_path:
        if element.direction == ProofDirection.LEFT:
            current_hash = compute_parent_hash(element.hash, current_hash)
        else:
            current_hash = compute_parent_hash(current_hash, element.hash)

    return current_hash
