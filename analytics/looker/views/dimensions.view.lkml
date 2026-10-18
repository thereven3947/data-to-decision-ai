view: dim_device {
  sql_table_name: d2d_analytics.dim_device ;;

  dimension: device_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.device_id ;;
  }
  dimension: kind {
    type: string
    sql: ${TABLE}.kind ;;
  }
  dimension: room {
    type: string
    sql: ${TABLE}.room ;;
  }
  dimension: circuit_id {
    type: string
    sql: ${TABLE}.circuit_id ;;
  }
  dimension: rated_w {
    type: number
    sql: ${TABLE}.rated_w ;;
  }

  measure: devices {
    type: count_distinct
    sql: ${device_id} ;;
  }
}

view: dim_category {
  sql_table_name: d2d_analytics.dim_category ;;

  dimension: category_id {
    primary_key: yes
    type: string
    sql: ${TABLE}.category_id ;;
  }
  dimension: name {
    type: string
    sql: ${TABLE}.name ;;
  }
  dimension: grid_analogue {
    type: string
    sql: ${TABLE}.grid_analogue ;;
  }
}
