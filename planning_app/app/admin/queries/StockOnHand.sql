select a.ProdCode, a.Description, a.StkQty
FROM FSTPA a
WHERE a.StkQty > 0
ORDER BY a.ProdCode ASC
