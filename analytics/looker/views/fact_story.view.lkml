view: fact_story {
  sql_table_name: d2d_analytics.fact_story ;;

  dimension: story_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.story_id ;;
  }
  dimension_group: window_start {
    type: time
    timeframes: [time, date, week, month]
    sql: ${TABLE}.window_start ;;
  }
  dimension: story_date {
    type: date
    sql: ${TABLE}.story_date ;;
  }
  dimension: category_id {
    type: string
    sql: COALESCE(${TABLE}.category_id, 'unlabeled') ;;
  }
  dimension: status {
    type: string
    sql: ${TABLE}.status ;;
  }
  dimension: label_source {
    type: string
    sql: ${TABLE}.label_source ;;
  }
  dimension: confidence {
    type: number
    value_format_name: percent_0
    sql: ${TABLE}.confidence ;;
  }
  dimension: device_count {
    type: number
    sql: ${TABLE}.device_count ;;
  }

  measure: stories {
    type: count
    drill_fields: [story_id, story_date, category_id, status]
  }
  measure: change_events {
    type: sum
    sql: ${TABLE}.event_count ;;
  }
  measure: auto_labeled {
    type: count
    filters: [label_source: "model"]
  }
  measure: review_backlog {
    type: count
    filters: [status: "needs_review"]
  }
  measure: auto_label_coverage {
    description: "Share of stories labeled by the model without human review (1 - abstain rate)."
    type: number
    value_format_name: percent_1
    sql: 1.0 * ${auto_labeled} / NULLIF(${stories}, 0) ;;
  }
}
