# Looker model over the D2D warehouse in BigQuery (see docs/analytics-warehouse.md).
# Metrics are defined once here (semantic layer) so every dashboard uses the same definitions.
connection: "d2d_bigquery"

include: "/views/*.view.lkml"

explore: fact_story {
  label: "Change stories"
  join: dim_category {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fact_story.category_id} = ${dim_category.category_id} ;;
  }
  join: fact_change_event {
    type: left_outer
    relationship: one_to_many
    sql_on: ${fact_change_event.story_id} = ${fact_story.story_id} ;;
  }
  join: dim_device {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fact_change_event.device_id} = ${dim_device.device_id} ;;
  }
}

explore: fact_security_alert {
  label: "Configuration integrity"
  join: dim_device {
    type: left_outer
    relationship: many_to_one
    sql_on: ${fact_security_alert.device_id} = ${dim_device.device_id} ;;
  }
}

explore: fact_agent_answer {
  label: "Copilot quality"
}
