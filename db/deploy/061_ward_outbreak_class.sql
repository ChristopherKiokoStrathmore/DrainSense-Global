-- Explicit outbreak map classification (page 41 + home KPI). Demo seed: apexmove/sim/cls/seed.sql (not deployed).
create or replace view v_ward_outbreak_class as
with sc as (
  select 'ALL' scope from dual
  union select distinct disease from coop_disease_reports),
c as (
  select sc.scope, r.ward_id, count(*) cases,
         sum(case when r.vet_status = 'VET_CONFIRMED' then 1 else 0 end) confirmed,
         listagg(distinct r.disease, ', ') within group (order by r.disease) diseases
  from sc join coop_disease_reports r on (sc.scope = 'ALL' or r.disease = sc.scope)
  where r.vet_status <> 'REJECTED' and r.reported_at > systimestamp - interval '14' day
  group by sc.scope, r.ward_id),
b as (
  select sc.scope, w.ward_id, w.ward_name, w.county, nvl(c.cases,0) cases, nvl(c.confirmed,0) confirmed, c.diseases,
         case when nvl(c.cases,0) >= 5 and nvl(c.confirmed,0) >= 3 then 3
              when nvl(c.cases,0) >= 2 then 2
              when nvl(c.cases,0) = 1 then 1 else 0 end base
  from sc cross join wards w left join c on c.scope = sc.scope and c.ward_id = w.ward_id),
adj as (
  select ward_id a, neighbour_id n from ward_neighbours
  union select neighbour_id, ward_id from ward_neighbours),
nr as (
  select b.scope, adj.n ward_id, listagg(distinct b.ward_name, ', ') within group (order by b.ward_name) red_nb
  from b join adj on adj.a = b.ward_id where b.base = 3 group by b.scope, adj.n),
rk as (
  select ward_id, listagg(distinct case hazard when 'rain_coming' then 'heavy rain' when 'field_flood' then 'field flooding' when 'rvf' then 'Rift Valley fever' else hazard end, ', ')
           within group (order by hazard) risks
  from v_ward_risk_latest where risk_level = 'HIGH' and hazard in ('rain_coming','field_flood','rvf') group by ward_id)
select b.scope, b.ward_id, b.ward_name, b.county, b.cases, b.confirmed, b.diseases, nr.red_nb, rk.risks,
       case when b.base = 1 and nr.red_nb is not null then 2
            when b.base > 0 then b.base
            when nr.red_nb is not null or rk.risks is not null then 1 else 0 end lvl,
       case when b.base = 3 then 'Affected: '||b.cases||' cases in 14 days ('||b.confirmed||' vet-confirmed)'
            when b.base = 2 then 'At risk: '||b.cases||' cases in 14 days ('||b.confirmed||' vet-confirmed)'
            when b.base = 1 and nr.red_nb is not null then 'At risk: 1 case, next to affected ward '||nr.red_nb
            when b.base = 1 then 'Watch: 1 case in 14 days'
            when nr.red_nb is not null then 'Watch: no cases, neighbour of affected ward '||nr.red_nb
            when rk.risks is not null then 'Watch: no cases, '||rk.risks||' risk (HIGH)'
            else 'Safe: no cases in 14 days, no high rain/flood risk' end reason
from b left join nr on nr.scope = b.scope and nr.ward_id = b.ward_id
       left join rk on rk.ward_id = b.ward_id
/
