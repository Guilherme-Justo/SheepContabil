from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_guide_tracks_the_current_evaluator_journey() -> None:
    guide = (ROOT / "docs" / "guia_estudo_e_demonstracao.md").read_text(encoding="utf-8")

    assert "Clique na aba **Fila de Revisão**" not in guide
    assert "Selecione um cliente ativo (ex.: *Horizonte Comércio Sintético*)" not in guide
    assert "[ PREFERENCIAL ]" not in guide
    assert "não simplesmente o mês da execução" in guide
    assert "(certificado, validade considerada, canal, política)" in guide
    assert "Aurora Demonstração Ltda." in guide
    assert "Mariana Souza Demo" in guide
    assert "python src/manage.py prepare_demo" in guide
    assert "Resultado: READY" in guide
    assert "cabeçalhos legados `demo-sc04-review`" in guide


def test_demo_guide_lists_all_canonical_module_routes() -> None:
    guide = (ROOT / "docs" / "guia_estudo_e_demonstracao.md").read_text(encoding="utf-8")

    for route in (
        "/modulos/triagem-caixa-arquivos/",
        "/modulos/bloqueio-clientes-inadimplentes/",
        "/modulos/briefing-societario/",
        "/modulos/vencimento-certificado-digital/",
    ):
        assert route in guide
