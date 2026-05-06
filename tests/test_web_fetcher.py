from knowledge_extractor_v3.fetchers.web import extract_page


def test_extract_page_prefers_article_body_and_metadata():
    html = """
    <html>
      <head>
        <title>Noise Title</title>
        <meta property="og:title" content="Real Article Title">
        <meta name="author" content="Reporter">
        <meta property="article:published_time" content="2026-04-28T12:00:00Z">
      </head>
      <body>
        <nav>Skip to content</nav>
        <article>
          <h1>Real Article Title</h1>
          <p>ScaleOps raised $130 million to improve Kubernetes efficiency for AI workloads.</p>
          <p>The company cited enterprise customers, cost savings, and rising compute demand.</p>
        </article>
      </body>
    </html>
    """

    page = extract_page(html)

    assert page.title == "Real Article Title"
    assert "ScaleOps raised $130 million" in page.text
    assert "Skip to content" not in page.text
    assert page.author == "Reporter"
    assert page.published_at == "2026-04-28T12:00:00Z"
