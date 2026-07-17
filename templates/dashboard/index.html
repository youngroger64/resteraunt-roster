{% extends "base.html" %}
{% block content %}
<div class="page-header">
  <div><h1>Dashboard</h1><p class="muted">Build next week's roster without touching the live clocking system.</p></div>
  <a class="btn primary" href="{% url 'roster:create' %}">Generate next week</a>
</div>
<div class="stats">
  <div class="card stat"><span>Active employees</span><strong>{{ employee_count }}</strong></div>
  <div class="card stat"><span>Draft shifts</span><strong>{{ draft_shift_count }}</strong></div>
  <div class="card stat"><span>Current draft</span><strong>{% if draft %}{{ draft.week_end|date:"d M" }}{% else %}—{% endif %}</strong></div>
  <div class="card stat"><span>Published</span><strong>{% if published %}{{ published.week_end|date:"d M" }}{% else %}—{% endif %}</strong></div>
</div>
<div class="grid-2">
  <section class="card">
    <h2>Monday morning</h2>
    {% if draft %}
      <p>Continue editing <strong>{{ draft }}</strong>.</p>
      <a class="btn" href="{% url 'roster:detail' draft.pk %}">Continue draft</a>
    {% else %}
      <p>No draft exists yet. Start from the latest roster or create a blank week.</p>
      <a class="btn" href="{% url 'roster:create' %}">Create draft</a>
    {% endif %}
  </section>
  <section class="card">
    <h2>Quick setup</h2>
    <p>Import employees, then import the default roster spreadsheet.</p>
    <a class="btn" href="{% url 'imports:index' %}">Open imports</a>
  </section>
</div>
{% endblock %}
