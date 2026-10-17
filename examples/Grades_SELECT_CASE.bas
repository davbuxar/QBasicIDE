INPUT "Marks (0-100)"; m
SELECT CASE m
    CASE 90 TO 100
        PRINT "Grade A"
    CASE 75 TO 89
        PRINT "Grade B"
    CASE 50 TO 74
        PRINT "Grade C"
    CASE ELSE
        PRINT "Fail"
END SELECT
