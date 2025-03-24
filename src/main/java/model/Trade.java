package model;

import java.math.BigInteger;
import java.time.LocalDateTime;

public record Trade(BigInteger id, java.util.UUID UUID, String trade, BigInteger amount, BigInteger currency,
                    BigInteger T1, BigInteger T2, BigInteger T3, BigInteger T4, BigInteger T5, BigInteger T6,
                    BigInteger T7, boolean bValue, LocalDateTime timestamp) {
}
