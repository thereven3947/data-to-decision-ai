view: fact_change_event {
  sql_table_name: d2d_analytics.fact_change_event ;;

  dimension: event_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.event_id ;;
  }
  dimension: story_id {
    type: string
    sql: ${TABLE}.story_id ;;
  }
  dimension: device_id {
    type: string
    sql: ${TABLE}.device_id ;;
  }
  dimension: property {
    type: string
    sql: ${TABLE}.property ;;
  }
  dimension: change_kind {
    type: string
    sql: ${TABLE}.change_kind ;;
  }
  dimension: semantic_tag {
    type: string
    sql: ${TABLE}.semantic_tag ;;
  }
  dimension_group: event {
    type: time
    timeframes: [time, hour_of_day, date, week]
    sql: ${TABLE}.event_ts ;;
  }

  measure: events {
    type: count
    drill_fields: [event_id, device_id, property, semantic_tag]
  }
}
