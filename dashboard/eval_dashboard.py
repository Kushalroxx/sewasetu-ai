import json
import os
from pathlib import Path

from flask import Blueprint, render_template_string

eval_dashboard_bp = Blueprint("eval_dashboard", __name__)

RESULTS_FILE = Path(
    os.getenv(
        "EVAL_RESULTS_FILE",
        "evals/results/results.json",
    )
)

@eval_dashboard_bp.get("/eval-dashboard")
def eval_dashboard():
    if RESULTS_FILE.exists():
        try:
            data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"evaluation_status": "unavailable", "results": []}
    else:
        data = {"evaluation_status": "not_run", "results": []}

    page = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Sewa Setu Evaluation Dashboard</title>
      <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 1200px; margin: 30px auto; padding: 0 16px; color: #222; }
        h1 { color: #1a3c6e; }
        .metric-banner { display: flex; gap: 20px; background: #f8f9fa; padding: 15px; border-radius: 6px; border: 1px solid #dee2e6; margin-bottom: 20px; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; table-layout: fixed; }
        th, td { border: 1px solid #ddd; padding: 12px; text-align: left; vertical-align: top; }
        th { background: #f8f9fa; font-weight: 600; }
        .passed { color: #0f5132; background-color: #d1e7dd; padding: 4px 8px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }
        .failed { color: #842029; background-color: #f8d7da; padding: 4px 8px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }
        .not_run { color: #664d03; background-color: #fff3cd; padding: 4px 8px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }
        .chat-bubble { padding: 10px 14px; border-radius: 6px; margin-bottom: 10px; font-size: 0.9em; line-height: 1.4; word-wrap: break-word; }
        .user { background: #e9ecef; border-left: 4px solid #6c757d; }
        .assistant { background: #e0f7fa; border-left: 4px solid #00acc1; }
        .tool { background: #fff3cd; border-left: 4px solid #ffc107; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.85em; white-space: pre-wrap; }
        .db-check { background: #212529; color: #32cd32; padding: 12px; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.85em; white-space: pre-wrap; overflow-x: auto; margin-top: 10px; line-height: 1.4; }
        .meta-text { font-size: 0.85em; color: #555; margin-top: 4px; }
      </style>
    </head>
    <body>
      <h1>Sewa Setu Evaluation Dashboard</h1>
      
      {% set results = data.get("results", []) %}
      {% set passed_count = results | selectattr("status", "equalto", "passed") | list | length %}
      {% set failed_count = results | selectattr("status", "equalto", "failed") | list | length %}
      {% set completed_count = passed_count + failed_count %}
      
      <div class="metric-banner">
        <div><strong>Status:</strong> {{ data.get("evaluation_status", "unknown") | upper }}</div>
        <div><strong>Pass Rate:</strong> 
            {% if completed_count %}
                {{ passed_count }}/{{ completed_count }} ({{ (100 * passed_count / completed_count) | round(1) }}%)
            {% else %}
                N/A
            {% endif %}
        </div>
      </div>

      <p>{{ data.get("note", "Results are based on the saved evaluation file.") }}</p>

      <table>
        <thead>
          <tr>
            <th style="width: 25%;">Citizen Persona & Goal</th>
            <th style="width: 10%;">Result</th>
            <th style="width: 65%;">Execution Trace & DB Assertions</th>
          </tr>
        </thead>
        <tbody>
        {% for item in results %}
          <tr>
            <td>
              <strong>{{ item.get("persona_name", item.get("persona_id", "")) }}</strong>
              <div class="meta-text">Age {{ item.get("age", "-") }} | {{ item.get("village", "-") }}, {{ item.get("block", "-") }}</div>
              <hr style="border: 0; border-top: 1px solid #eee; margin: 12px 0;">
              <strong>Goal:</strong><br>
              <div class="meta-text">{{ item.get("goal", "") }}</div>
              <br>
              <strong>Expected State:</strong><br>
              <div class="meta-text">{{ item.get("expected_portal_state", "") }}</div>
            </td>
            <td><span class="{{ item.get('status', 'unknown') }}">{{ item.get("status", "unknown") }}</span></td>
            <td>
              <strong>Conversation Log:</strong>
              <div style="margin-top: 10px;">
              {% for turn in item.get("conversation", []) %}
                {% if turn.role == 'user' %}
                    <div class="chat-bubble user"><strong>Citizen:</strong><br>{{ turn.content }}</div>
                {% elif turn.role == 'assistant' %}
                    <div class="chat-bubble assistant"><strong>Agent:</strong><br>{{ turn.content }}</div>
                {% elif turn.role == 'tool' %}
                    <div class="chat-bubble tool"><strong>Tool Called:</strong> {{ turn.name }}
<strong>Arguments:</strong> {{ turn.args | tojson(indent=2) }}
<strong>Returned:</strong> {{ turn.result | tojson(indent=2) }}</div>
                {% endif %}
              {% endfor %}
              </div>
              
              <hr style="border: 0; border-top: 1px dashed #ccc; margin: 20px 0;">
              <strong>Database Assertions:</strong>
              {% for check in item.get("checks", []) %}
                <div class="db-check">-- {{ check.description }}
{{ check.sql }}
>> Result:   {{ check.result }}
>> Expected: {{ check.expected }}
>> Status:   {% if check.passed %}PASS{% else %}FAIL{% endif %}</div>
              {% else %}
                <p><small>No database assertions recorded for this persona.</small></p>
              {% endfor %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="3">No evaluation results have been recorded.</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <p class="meta-text" style="margin-top: 20px;">Generated at: {{ data.get("generated_at", "Not available") }}</p>
    </body>
    </html>
    """
    return render_template_string(page, data=data)