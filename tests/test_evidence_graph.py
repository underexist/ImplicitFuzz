import sqlite3

from implicitfuzz.evidence.graph import (
    build_evidence_graph,
    create_evidence_tables,
    derive_access_nodes,
    derive_lifecycle_edges,
    derive_state_flow_edges,
    summarize_evidence_graph,
)


def test_create_evidence_tables_creates_expected_tables():
    conn = sqlite3.connect(":memory:")
    create_evidence_tables(conn)

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert "evidence_node" in tables
    assert "evidence_edge" in tables


def _create_access_fact_table(conn):
    conn.executescript(
        """
        CREATE TABLE access_fact (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          bc_unit TEXT NOT NULL,
          function TEXT NOT NULL,
          semantic_op TEXT NOT NULL,
          access_kind TEXT NOT NULL,
          access_path_symbolic TEXT,
          field_type TEXT,
          numeric_kind TEXT NOT NULL,
          access_path_numeric TEXT,
          primary_provenance TEXT NOT NULL,
          confidence TEXT NOT NULL,
          summary_detail_json TEXT,
          source_location_json TEXT
        );
        """
    )


def _insert_access(
    conn,
    *,
    bc_unit="unit.bc",
    function="fn",
    semantic_op="read",
    access_kind="direct_load",
    symbolic="Node.value",
    field_type="int",
    numeric_kind="gep_offsets",
    numeric="[0,0]",
    provenance="dwarf",
    confidence="high",
    summary_detail=None,
):
    conn.execute(
        """
        INSERT INTO access_fact (
          bc_unit, function, semantic_op, access_kind,
          access_path_symbolic, field_type, numeric_kind, access_path_numeric,
          primary_provenance, confidence, summary_detail_json, source_location_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bc_unit,
            function,
            semantic_op,
            access_kind,
            symbolic,
            field_type,
            numeric_kind,
            numeric,
            provenance,
            confidence,
            summary_detail,
            '{"spelling":"input.c:1","expansion":"input.c:1","inlined_at":[]}',
        ),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_derive_access_nodes_creates_one_node_per_access_fact():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)
    create_evidence_tables(conn)

    first_id = _insert_access(conn, semantic_op="write", symbolic="Node.value")
    second_id = _insert_access(
        conn,
        semantic_op="read",
        symbolic=None,
        numeric_kind="whole_object",
        numeric="[]",
    )

    inserted = derive_access_nodes(conn)

    assert inserted == 2
    rows = conn.execute(
        "SELECT fact_id, node_kind, node_key, label, confidence FROM evidence_node ORDER BY fact_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (first_id, "access", f"access_fact:{first_id}", "Node.value", "high"),
        (second_id, "access", f"access_fact:{second_id}", "whole_object:[]", "high"),
    ]


def test_derive_state_flow_edges_links_write_before_read_on_same_symbolic_field():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)
    create_evidence_tables(conn)

    write_id = _insert_access(conn, function="producer", semantic_op="write", symbolic="Node.value")
    read_id = _insert_access(conn, function="consumer", semantic_op="read", symbolic="Node.value")
    _insert_access(conn, function="other", semantic_op="read", symbolic="Node.next")
    derive_access_nodes(conn)

    inserted = derive_state_flow_edges(conn)

    assert inserted == 1
    row = conn.execute(
        """
        SELECT edge_kind, source_fact_id, target_fact_id, basis, confidence
        FROM evidence_edge
        """
    ).fetchone()
    assert tuple(row) == (
        "state_write_read_candidate",
        write_id,
        read_id,
        "same_bc_unit_and_symbolic_field",
        "high",
    )


def test_derive_state_flow_edges_does_not_link_numeric_only_facts():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)
    create_evidence_tables(conn)

    _insert_access(conn, semantic_op="write", symbolic=None, numeric_kind="whole_object", numeric="[]")
    _insert_access(conn, semantic_op="read", symbolic=None, numeric_kind="whole_object", numeric="[]")
    derive_access_nodes(conn)

    inserted = derive_state_flow_edges(conn)

    assert inserted == 0
    count = conn.execute("SELECT COUNT(*) FROM evidence_edge").fetchone()[0]
    assert count == 0


def test_derive_lifecycle_edges_links_alloc_to_free_in_same_function():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)
    create_evidence_tables(conn)

    alloc_id = _insert_access(
        conn,
        function="io_provide_buffers",
        semantic_op="alloc",
        access_kind="call_alloc",
        symbolic=None,
        numeric_kind="unknown",
        numeric=None,
        provenance="summary",
        confidence="high",
    )
    free_id = _insert_access(
        conn,
        function="io_provide_buffers",
        semantic_op="free",
        access_kind="call_free",
        symbolic=None,
        numeric_kind="unknown",
        numeric=None,
        provenance="summary",
        confidence="high",
    )
    _insert_access(
        conn,
        function="other_function",
        semantic_op="free",
        access_kind="call_free",
        symbolic=None,
        numeric_kind="unknown",
        numeric=None,
        provenance="summary",
        confidence="high",
    )
    derive_access_nodes(conn)

    inserted = derive_lifecycle_edges(conn)

    assert inserted == 1
    row = conn.execute(
        """
        SELECT edge_kind, source_fact_id, target_fact_id, basis, confidence
        FROM evidence_edge
        """
    ).fetchone()
    assert tuple(row) == (
        "lifecycle_candidate",
        alloc_id,
        free_id,
        "same_function_alloc_before_free",
        "high",
    )


def test_build_evidence_graph_returns_counts_and_summary():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)

    _insert_access(conn, function="producer", semantic_op="write", symbolic="Node.value")
    _insert_access(conn, function="consumer", semantic_op="read", symbolic="Node.value")
    _insert_access(
        conn,
        function="io_provide_buffers",
        semantic_op="alloc",
        access_kind="call_alloc",
        symbolic=None,
        numeric_kind="unknown",
        numeric=None,
        provenance="summary",
    )
    _insert_access(
        conn,
        function="io_provide_buffers",
        semantic_op="free",
        access_kind="call_free",
        symbolic=None,
        numeric_kind="unknown",
        numeric=None,
        provenance="summary",
    )

    counts = build_evidence_graph(conn)
    summary = summarize_evidence_graph(conn)

    assert counts == {
        "access_nodes": 4,
        "state_write_read_candidate_edges": 1,
        "lifecycle_candidate_edges": 1,
    }
    assert summary["nodes"] == {"access": 4}
    assert summary["edges"] == {
        "lifecycle_candidate": 1,
        "state_write_read_candidate": 1,
    }


def test_build_evidence_graph_is_idempotent():
    conn = sqlite3.connect(":memory:")
    _create_access_fact_table(conn)

    _insert_access(conn, function="producer", semantic_op="write", symbolic="Node.value")
    _insert_access(conn, function="consumer", semantic_op="read", symbolic="Node.value")

    first_counts = build_evidence_graph(conn)
    second_counts = build_evidence_graph(conn)
    summary = summarize_evidence_graph(conn)

    assert first_counts == {
        "access_nodes": 2,
        "state_write_read_candidate_edges": 1,
        "lifecycle_candidate_edges": 0,
    }
    assert second_counts == {
        "access_nodes": 0,
        "state_write_read_candidate_edges": 0,
        "lifecycle_candidate_edges": 0,
    }
    assert summary == {
        "nodes": {"access": 2},
        "edges": {"state_write_read_candidate": 1},
    }
