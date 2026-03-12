SELECT DISTINCT j.Customer AS CustID, 
a.CUSPRODREF AS BatchID, 
a.ORDERNUMBER AS WorksOrder, a.SectionDesc, a.LOADDATE AS LoadDate, a.DUEDATE AS DueDate,
h.ProdCode AS MaterialCode, g.Description AS MaterialDesc,
CAST((g.RqdQty/a.NumberSets)*a.Qty as numeric(10,2)) AS QtyRequiredForSets, g.RqdQty AS QtyForOrder, g.IssQty AS QtyIssued, l.ProdGrp, l.PgDescription, g.Complete 
FROM FCADD a
INNER JOIN FCASOHE b ON a.SOHEKEY = b.SOHEKEY
INNER JOIN FSODE c ON a.SOHEKEY = c.SOHEKEY
INNER JOIN FCAGC k ON c.CaravanSec_CagcKey = k.CagcKey AND k.KeyField = a.Section
LEFT OUTER JOIN FSTPA d ON c.STPAKEY = d.STPAKEY
INNER JOIN FCSCP e ON d.STPAKEY = e.STPAKEY
INNER JOIN FCACM f ON a.CacmKey = f.CacmKey
INNER JOIN FCASOSR g ON a.SoheKey = g.SoheKey
INNER JOIN FSTPA h ON g.StpaKey = h.StpaKey
INNER JOIN FCSMT i ON e.CompNo = i.CompNo AND e.CacmKey = i.CacmKey AND g.StpaKey = i.StpaKey
INNER JOIN FSOSM j ON a.SosmKey = j.SosmKey
INNER JOIN FSTPG l ON h.StpgKey = l.StpgKey
WHERE b.MANUFACTPROCESS='M' AND c.DETYPE='G' AND c.OrdQty > c.InvQty AND g.Complete = 'N' 
AND c.ORDQTY > 0 
AND i.Cancelled = 'N'
AND e.CompNo <> 0
ORDER BY a.LOADDATE, a.CUSTOMER, a.CUSPRODREF, a.ORDERNUMBER
