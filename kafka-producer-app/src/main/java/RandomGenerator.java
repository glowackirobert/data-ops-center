import java.math.BigInteger;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Random;
import java.util.concurrent.ThreadLocalRandom;

public class RandomGenerator {

    private static final Random RANDOM = new Random();
    private static final String CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    public static String generateStringValue() {
        var sb = new StringBuilder(5);
        for (int i = 0; i < 5; i++) {
            var randomIndex = RANDOM.nextInt(CHARACTERS.length());
            sb.append(CHARACTERS.charAt(randomIndex));
        }
        return sb.toString();
    }

    public static BigInteger generateBigIntegerValue(int bits) {
        return new BigInteger(bits, RANDOM);
    }

    public static boolean generateBooleanValue() {
        return RANDOM.nextBoolean();
    }

    public static <T extends Enum<?>> T getRandomEnumValue(Class<T> enumClass) {
        T[] enumValues = enumClass.getEnumConstants();
        int randomIndex = RANDOM.nextInt(enumValues.length);
        return enumValues[randomIndex];
    }

    public static long generateRandomNanoTimestamp() {
        Instant now = Instant.now();
        Instant oneYearAgo = now.minus(365, ChronoUnit.DAYS);

        long startEpochNano = oneYearAgo.getEpochSecond() * 1_000_000_000L + oneYearAgo.getNano();
        long endEpochNano = now.getEpochSecond() * 1_000_000_000L + now.getNano();

        return ThreadLocalRandom.current().nextLong(startEpochNano, endEpochNano);
    }
}
