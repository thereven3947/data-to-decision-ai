view: fact_security_alert {
  sql_table_name: d2d_analytics.fact_security_alert ;;

  dimension: event_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.event_id ;;
  }
  dimension: device_id {
    type: string
    sql: ${TABLE}.device_id ;;
  }
  dimension: property {
    type: string
    sql: ${TABLE}.property ;;
  }
  dimension: reasons {
    type: string
    sql: ${TABLE}.reasons ;;
  }
  dimension: score {
    type: number
    value_format_name: decimal_2
    sql: ${TABLE}.score ;;
  }
  dimension: is_alert {
    type: yesno
    sql: ${TABLE}.alert ;;
  }
  dimension_group: event {
    type: time
    timeframes: [time, date, week]
    sql: ${TABLE}.event_ts ;;
  }

  measure: changes_scored {
    type: count
  }
  measure: alerts {
    type: count
    filters: [is_alert: "yes"]
    drill_fields: [event_id, device_id, property, score, reasons]
  }
}

view: fact_agent_answer {
  sql_table_name: d2d_analytics.fact_agent_answer ;;

  dimension: message_id {
    primary_key: yes
    type: number
    sql: ${TABLE}.message_id ;;
  }
  dimension: skill {
    type: string
    sql: ${TABLE}.skill ;;
  }
  dimension: user_role {
    type: string
    sql: ${TABLE}.user_role ;;
  }
  dimension: llm_model_tag {
    type: string
    sql: ${TABLE}.llm_model_tag ;;
  }
  dimension: grounded {
    type: yesno
    sql: ${TABLE}.grounded ;;
  }
  dimension_group: created {
    type: time
    timeframes: [time, date, week]
    sql: ${TABLE}.created_at ;;
  }

  measure: answers {
    type: count
  }
  measure: grounded_answers {
    type: count
    filters: [grounded: "yes"]
  }
  measure: grounded_rate {
    description: "Share of answers that passed the grounding checker (eval gate: >= 0.98)."
    type: number
    value_format_name: percent_1
    sql: 1.0 * ${grounded_answers} / NULLIF(${answers}, 0) ;;
  }
  measure: p95_latency_ms {
    type: percentile
    percentile: 95
    sql: ${TABLE}.latency_ms ;;
  }
  measure: avg_tool_calls {
    type: average
    value_format_name: decimal_1
    sql: ${TABLE}.tool_calls ;;
  }
}
