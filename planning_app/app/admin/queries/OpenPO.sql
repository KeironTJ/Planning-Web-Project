select d.popno, e.Supplier, e.Name, a.orditem, b.prodcode, a.pldescription, SUM(a.ordqty - a.recqty) AS OustandingQty, 
c.DueDate
from fpopl a
inner join fstpa b on a.stpakey = b.stpakey
inner join fpopc c on a.orditem = c.orditem and a.popno = c.popno
inner join fpoph d on a.popno = d.popno
inner join fpopm e on d.popmkey = e.popmkey
where a.osqty > 0 and a.cancelled = 'N' and c.pcstatus NOT IN (99,5) AND plType = 'G' and d.cancelled = 'N' 
GROUP BY d.popno, e.Supplier, e.Name, a.orditem, b.prodcode, a.pldescription, c.duedate
ORDER BY c.DueDate, d.popno, a.orditem ASC