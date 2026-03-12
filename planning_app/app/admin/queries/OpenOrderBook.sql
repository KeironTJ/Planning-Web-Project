select DISTINCT a.SopNo, e.Customer, e.Name, d.CustordRef, f.CusProdRef, 
CASE WHEN f.ManufactProcess = 'M' THEN 'MAINLINE' ELSE 'AFTERSALES' END AS OrderType,
CASE WHEN g.CaravanCode IS NULL THEN '' ELSE g.CaravanCode END AS CaravanCode,
CASE WHEN g.Description IS NULL THEN '' ELSE g.Description END AS CaravanDescription,
a.OrdItem, d.OrdDate, a.DueDate,
c.ProdCode, 
CASE WHEN c.Description = 'D/S/L FOLDER AS' THEN 'D/S/L AS'
WHEN c.Description = 'FOOTSTOOL-UPH' THEN 'STOOL-UPH'
WHEN c.Description = 'NET' THEN 'NET/VOILE'
WHEN c.Description = 'VERTICAL BLIND' THEN 'VERTICAL BLIND-BI'
WHEN c.Description = 'VOILE' THEN 'VOILE/NET'
ELSE c.Description END AS Description, 
a.Qty, 
CASE 
WHEN l.WcDescription IS NULL THEN UPPER(i.SectionDesc)
WHEN l.WcDescription = 'NET SECTION' THEN 'CURTAINS'
ELSE l.WcDescription END AS WorkCentre,
b.Nettprice as SellPrice,
a.Qty*b.Nettprice as TotalValue
FROM FSOSC a
INNER JOIN FSODE b ON a.SoheKey = b.SoheKey AND a.OrdItem = b.OrdItem
LEFT OUTER JOIN FSTPA c ON a.StpaKey = c.StpaKey
INNER JOIN FSOHE d ON a.SoheKey = d.SoheKey
INNER JOIN FSOSM e ON d.SosmKey = e.SosmKey
LEFT OUTER JOIN FCASOHE f ON a.SoheKey = f.SoheKey
LEFT OUTER JOIN FCACM g ON f.CacmKey = g.CacmKey
LEFT OUTER JOIN FCAGC h ON b.UOM = h.KeyField AND h.CodeType = 'UM'
INNER JOIN FCADD i ON a.SopNo = i.SopNo 
LEFT OUTER JOIN FCSCP j ON a.StpaKey = j.StpaKey
LEFT OUTER JOIN FCACW k ON j.Cacpkey = k.Cacpkey
LEFT OUTER JOIN FSTWC l ON k.Stwckey = l.Stwckey 
WHERE b.Cancelled = 'N' AND d.Cancelled = 'N' AND a.InvNo = 0 AND a.DueDate >= '2020/06/01' AND a.Qty > 0 AND a.ScStatus <=6
ORDER BY a.DueDate, a.SopNo, a.OrdItem, WorkCentre ASC
