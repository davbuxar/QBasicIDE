DECLARE FUNCTION Fact& (n%)
INPUT "Number"; n%
PRINT n%; "! ="; Fact&(n%)
END

FUNCTION Fact& (n%)
    IF n% <= 1 THEN
        Fact& = 1
    ELSE
        Fact& = n% * Fact&(n% - 1)
    END IF
END FUNCTION
