"""
Unit tests for the Merkle Tree implementation.

Includes test vectors and edge case coverage.
"""

import hashlib

import pytest

from app.crypto.merkle import (
    LEAF_PREFIX,
    NODE_PREFIX,
    MerkleProof,
    MerkleTree,
    ProofDirection,
    ProofElement,
    compute_leaf_hash,
    compute_parent_hash,
    compute_root_from_proof,
    verify_proof,
    verify_proof_against_root,
)


class TestHashFunctions:
    """Tests for hash computation functions."""

    def test_compute_leaf_hash_bytes(self) -> None:
        """Test leaf hash computation with bytes input."""
        data = b"hello"
        result = compute_leaf_hash(data)

        # Verify manually
        expected = hashlib.sha256(LEAF_PREFIX + data).hexdigest()
        assert result == expected

    def test_compute_leaf_hash_string(self) -> None:
        """Test leaf hash computation with string input."""
        data = "hello"
        result = compute_leaf_hash(data)

        # String should be encoded to bytes
        expected = hashlib.sha256(LEAF_PREFIX + data.encode("utf-8")).hexdigest()
        assert result == expected

    def test_compute_leaf_hash_hex_string(self) -> None:
        """Test leaf hash computation with hex string."""
        hex_data = "deadbeef"
        result = compute_leaf_hash(hex_data)

        # Hex string should be converted to bytes
        expected = hashlib.sha256(LEAF_PREFIX + bytes.fromhex(hex_data)).hexdigest()
        assert result == expected

    def test_compute_leaf_hash_deterministic(self) -> None:
        """Test that leaf hash is deterministic."""
        data = b"test data"
        assert compute_leaf_hash(data) == compute_leaf_hash(data)

    def test_compute_parent_hash(self) -> None:
        """Test parent hash computation."""
        left = "a" * 64
        right = "b" * 64

        result = compute_parent_hash(left, right)

        # Verify manually
        expected = hashlib.sha256(
            NODE_PREFIX + bytes.fromhex(left) + bytes.fromhex(right)
        ).hexdigest()
        assert result == expected

    def test_compute_parent_hash_deterministic(self) -> None:
        """Test that parent hash is deterministic."""
        left = "a" * 64
        right = "b" * 64
        assert compute_parent_hash(left, right) == compute_parent_hash(left, right)

    def test_domain_separation(self) -> None:
        """Test that leaf and node hashes use different prefixes."""
        data = b"test"

        leaf_hash = compute_leaf_hash(data)

        # If we hash the same data as a "node", we should get different result
        as_hex = data.hex()
        # Pad to 64 chars for valid node hash
        padded = as_hex.ljust(64, "0")
        parent_hash = compute_parent_hash(padded, padded)

        assert leaf_hash != parent_hash


class TestMerkleTree:
    """Tests for MerkleTree construction."""

    def test_single_leaf(self) -> None:
        """Test tree with single leaf."""
        tree = MerkleTree.from_leaves([b"only"])

        assert tree.leaf_count == 1
        assert tree.root_hash == compute_leaf_hash(b"only")

    def test_two_leaves(self) -> None:
        """Test tree with two leaves."""
        tree = MerkleTree.from_leaves([b"a", b"b"])

        assert tree.leaf_count == 2

        left_hash = compute_leaf_hash(b"a")
        right_hash = compute_leaf_hash(b"b")
        expected_root = compute_parent_hash(left_hash, right_hash)

        assert tree.root_hash == expected_root

    def test_four_leaves(self) -> None:
        """Test tree with four leaves (perfect binary tree)."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])

        assert tree.leaf_count == 4

        # Build expected tree
        h0 = compute_leaf_hash(b"a")
        h1 = compute_leaf_hash(b"b")
        h2 = compute_leaf_hash(b"c")
        h3 = compute_leaf_hash(b"d")

        h01 = compute_parent_hash(h0, h1)
        h23 = compute_parent_hash(h2, h3)
        expected_root = compute_parent_hash(h01, h23)

        assert tree.root_hash == expected_root

    def test_three_leaves_odd(self) -> None:
        """Test tree with odd number of leaves."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c"])

        assert tree.leaf_count == 3

        # With promotion strategy:
        # Level 0: h0, h1, h2
        # Level 1: h01, h2 (promoted)
        # Level 2: root = hash(h01, h2)
        h0 = compute_leaf_hash(b"a")
        h1 = compute_leaf_hash(b"b")
        h2 = compute_leaf_hash(b"c")

        h01 = compute_parent_hash(h0, h1)
        expected_root = compute_parent_hash(h01, h2)

        assert tree.root_hash == expected_root

    def test_five_leaves_odd(self) -> None:
        """Test tree with five leaves."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d", b"e"])

        assert tree.leaf_count == 5

    def test_seven_leaves_odd(self) -> None:
        """Test tree with seven leaves."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d", b"e", b"f", b"g"])

        assert tree.leaf_count == 7

    def test_empty_leaves_raises(self) -> None:
        """Test that empty leaves raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            MerkleTree.from_leaves([])

    def test_from_hashes(self) -> None:
        """Test construction from pre-computed hashes."""
        hashes = [
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
        ]
        tree = MerkleTree.from_hashes(hashes)

        assert tree.leaf_count == 4

    def test_from_raw_hashes(self) -> None:
        """Test construction using hashes directly as leaves."""
        hashes = [
            compute_leaf_hash(b"a"),
            compute_leaf_hash(b"b"),
        ]
        tree = MerkleTree.from_raw_hashes(hashes)

        assert tree.leaf_count == 2
        assert tree.get_leaf_hash(0) == hashes[0]
        assert tree.get_leaf_hash(1) == hashes[1]

    def test_get_leaf_hash(self) -> None:
        """Test getting leaf hash by index."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c"])

        assert tree.get_leaf_hash(0) == compute_leaf_hash(b"a")
        assert tree.get_leaf_hash(1) == compute_leaf_hash(b"b")
        assert tree.get_leaf_hash(2) == compute_leaf_hash(b"c")

    def test_get_leaf_hash_out_of_bounds(self) -> None:
        """Test that out-of-bounds index raises."""
        tree = MerkleTree.from_leaves([b"a", b"b"])

        with pytest.raises(IndexError):
            tree.get_leaf_hash(5)

        with pytest.raises(IndexError):
            tree.get_leaf_hash(-1)

    def test_deterministic_root(self) -> None:
        """Test that same leaves produce same root."""
        leaves = [b"a", b"b", b"c", b"d"]
        tree1 = MerkleTree.from_leaves(leaves)
        tree2 = MerkleTree.from_leaves(leaves)

        assert tree1.root_hash == tree2.root_hash

    def test_different_order_different_root(self) -> None:
        """Test that different order produces different root."""
        tree1 = MerkleTree.from_leaves([b"a", b"b"])
        tree2 = MerkleTree.from_leaves([b"b", b"a"])

        assert tree1.root_hash != tree2.root_hash


class TestProofGeneration:
    """Tests for proof generation."""

    def test_proof_single_leaf(self) -> None:
        """Test proof for single leaf tree."""
        tree = MerkleTree.from_leaves([b"only"])
        proof = tree.get_proof(0)

        assert proof.leaf_hash == compute_leaf_hash(b"only")
        assert proof.leaf_index == 0
        assert len(proof.proof_path) == 0
        assert proof.root_hash == tree.root_hash
        assert proof.tree_size == 1

    def test_proof_two_leaves(self) -> None:
        """Test proof generation for two-leaf tree."""
        tree = MerkleTree.from_leaves([b"a", b"b"])

        # Proof for first leaf
        proof0 = tree.get_proof(0)
        assert proof0.leaf_index == 0
        assert len(proof0.proof_path) == 1
        assert proof0.proof_path[0].direction == ProofDirection.RIGHT
        assert proof0.proof_path[0].hash == compute_leaf_hash(b"b")

        # Proof for second leaf
        proof1 = tree.get_proof(1)
        assert proof1.leaf_index == 1
        assert len(proof1.proof_path) == 1
        assert proof1.proof_path[0].direction == ProofDirection.LEFT
        assert proof1.proof_path[0].hash == compute_leaf_hash(b"a")

    def test_proof_four_leaves(self) -> None:
        """Test proof generation for four-leaf tree."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])

        # Proof for first leaf should have 2 elements
        proof0 = tree.get_proof(0)
        assert len(proof0.proof_path) == 2

        # Proof for last leaf
        proof3 = tree.get_proof(3)
        assert len(proof3.proof_path) == 2

    def test_proof_out_of_bounds(self) -> None:
        """Test that out-of-bounds proof request raises."""
        tree = MerkleTree.from_leaves([b"a", b"b"])

        with pytest.raises(IndexError):
            tree.get_proof(5)

    def test_get_all_proofs(self) -> None:
        """Test getting all proofs at once."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proofs = tree.get_all_proofs()

        assert len(proofs) == 4
        for i, proof in enumerate(proofs):
            assert proof.leaf_index == i


class TestProofVerification:
    """Tests for proof verification."""

    def test_verify_single_leaf(self) -> None:
        """Test verification for single leaf."""
        tree = MerkleTree.from_leaves([b"only"])
        proof = tree.get_proof(0)

        assert verify_proof(proof)

    def test_verify_two_leaves(self) -> None:
        """Test verification for two-leaf tree."""
        tree = MerkleTree.from_leaves([b"a", b"b"])

        assert verify_proof(tree.get_proof(0))
        assert verify_proof(tree.get_proof(1))

    def test_verify_four_leaves(self) -> None:
        """Test verification for all leaves in four-leaf tree."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])

        for i in range(4):
            proof = tree.get_proof(i)
            assert verify_proof(proof), f"Proof for leaf {i} failed"

    def test_verify_odd_leaves(self) -> None:
        """Test verification for odd number of leaves."""
        for count in [3, 5, 7, 9, 11]:
            leaves = [f"leaf{i}".encode() for i in range(count)]
            tree = MerkleTree.from_leaves(leaves)

            for i in range(count):
                proof = tree.get_proof(i)
                assert verify_proof(proof), f"Proof failed for leaf {i} in {count}-leaf tree"

    def test_verify_large_tree(self) -> None:
        """Test verification for larger tree."""
        leaves = [f"data{i}".encode() for i in range(100)]
        tree = MerkleTree.from_leaves(leaves)

        # Verify random subset
        for i in [0, 25, 50, 75, 99]:
            proof = tree.get_proof(i)
            assert verify_proof(proof)

    def test_verify_tampered_leaf_fails(self) -> None:
        """Test that tampered leaf hash fails verification."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(0)

        # Tamper with leaf hash
        tampered = MerkleProof(
            leaf_hash="0" * 64,  # Wrong hash
            leaf_index=proof.leaf_index,
            proof_path=proof.proof_path,
            root_hash=proof.root_hash,
            tree_size=proof.tree_size,
        )

        assert not verify_proof(tampered)

    def test_verify_tampered_proof_fails(self) -> None:
        """Test that tampered proof path fails verification."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(0)

        # Tamper with proof path
        tampered_path = [
            ProofElement(hash="0" * 64, direction=proof.proof_path[0].direction)
        ] + proof.proof_path[1:]

        tampered = MerkleProof(
            leaf_hash=proof.leaf_hash,
            leaf_index=proof.leaf_index,
            proof_path=tampered_path,
            root_hash=proof.root_hash,
            tree_size=proof.tree_size,
        )

        assert not verify_proof(tampered)

    def test_verify_proof_against_root(self) -> None:
        """Test verification against specific root."""
        tree = MerkleTree.from_leaves([b"a", b"b"])
        proof = tree.get_proof(0)

        assert verify_proof_against_root(
            proof.leaf_hash,
            proof.proof_path,
            tree.root_hash,
        )

        # Wrong root should fail
        assert not verify_proof_against_root(
            proof.leaf_hash,
            proof.proof_path,
            "0" * 64,
        )

    def test_compute_root_from_proof(self) -> None:
        """Test computing root from proof."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(2)

        computed = compute_root_from_proof(proof.leaf_hash, proof.proof_path)
        assert computed == tree.root_hash


class TestProofSerialization:
    """Tests for proof serialization/deserialization."""

    def test_proof_to_dict(self) -> None:
        """Test proof serialization to dictionary."""
        tree = MerkleTree.from_leaves([b"a", b"b"])
        proof = tree.get_proof(0)

        data = proof.to_dict()

        assert data["leaf_hash"] == proof.leaf_hash
        assert data["leaf_index"] == 0
        assert len(data["proof_path"]) == 1
        assert data["root_hash"] == proof.root_hash
        assert data["tree_size"] == 2

    def test_proof_from_dict(self) -> None:
        """Test proof deserialization from dictionary."""
        tree = MerkleTree.from_leaves([b"a", b"b"])
        proof = tree.get_proof(0)

        data = proof.to_dict()
        restored = MerkleProof.from_dict(data)

        assert restored.leaf_hash == proof.leaf_hash
        assert restored.leaf_index == proof.leaf_index
        assert restored.root_hash == proof.root_hash
        assert verify_proof(restored)

    def test_proof_to_compact(self) -> None:
        """Test compact serialization."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(0)

        compact = proof.to_compact()

        assert len(compact) == 2
        assert all(":" in item for item in compact)

    def test_proof_from_compact(self) -> None:
        """Test compact deserialization."""
        tree = MerkleTree.from_leaves([b"a", b"b", b"c", b"d"])
        proof = tree.get_proof(0)

        compact = proof.to_compact()
        restored = MerkleProof.from_compact(
            leaf_hash=proof.leaf_hash,
            leaf_index=proof.leaf_index,
            compact_path=compact,
            root_hash=proof.root_hash,
            tree_size=proof.tree_size,
        )

        assert verify_proof(restored)


class TestProofElement:
    """Tests for ProofElement."""

    def test_to_dict(self) -> None:
        """Test element serialization."""
        element = ProofElement(hash="a" * 64, direction=ProofDirection.LEFT)
        data = element.to_dict()

        assert data["hash"] == "a" * 64
        assert data["direction"] == "L"

    def test_from_dict(self) -> None:
        """Test element deserialization."""
        data = {"hash": "b" * 64, "direction": "R"}
        element = ProofElement.from_dict(data)

        assert element.hash == "b" * 64
        assert element.direction == ProofDirection.RIGHT


class TestKnownVectors:
    """Tests using known test vectors for interoperability."""

    def test_vector_empty_leaf(self) -> None:
        """Test with empty byte string leaf."""
        tree = MerkleTree.from_leaves([b""])
        proof = tree.get_proof(0)
        assert verify_proof(proof)

    def test_vector_unicode_data(self) -> None:
        """Test with unicode data."""
        tree = MerkleTree.from_leaves(["hello 世界".encode("utf-8")])
        proof = tree.get_proof(0)
        assert verify_proof(proof)

    def test_vector_binary_data(self) -> None:
        """Test with binary data."""
        data = bytes(range(256))
        tree = MerkleTree.from_leaves([data])
        proof = tree.get_proof(0)
        assert verify_proof(proof)

    def test_vector_large_leaves(self) -> None:
        """Test with large leaf data."""
        large_data = b"x" * 10000
        tree = MerkleTree.from_leaves([large_data, b"small"])
        assert verify_proof(tree.get_proof(0))
        assert verify_proof(tree.get_proof(1))

    def test_vector_power_of_two_leaves(self) -> None:
        """Test with power-of-two leaf counts."""
        for count in [2, 4, 8, 16, 32]:
            leaves = [f"leaf{i}".encode() for i in range(count)]
            tree = MerkleTree.from_leaves(leaves)
            for i in range(count):
                assert verify_proof(tree.get_proof(i))

    def test_consistency_across_constructions(self) -> None:
        """Test that different construction methods yield same root."""
        data = [b"a", b"b", b"c", b"d"]

        tree1 = MerkleTree.from_leaves(data)

        # Pre-compute hashes and construct
        hashes = [compute_leaf_hash(d) for d in data]
        tree2 = MerkleTree.from_raw_hashes(hashes)

        assert tree1.root_hash == tree2.root_hash
