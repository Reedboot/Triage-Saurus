using Azure.Identity;
using Microsoft.Data.SqlClient;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddApplicationInsightsTelemetry();

var keyVaultUri = builder.Configuration["KeyVaultUri"];
if (!string.IsNullOrWhiteSpace(keyVaultUri))
{
    builder.Configuration.AddAzureKeyVault(new Uri(keyVaultUri), new DefaultAzureCredential());
}

builder.Services.AddHealthChecks();

var app = builder.Build();

app.MapGet("/", () => Results.Ok(new { service = "SecureWebApp", status = "ok" }));
app.MapHealthChecks("/healthz");

app.MapGet("/api/users/search", async (string? name, IConfiguration config, CancellationToken ct) =>
{
    var (connString, error) = BuildSqlConnectionString(config);
    if (error is not null) return Results.Problem(error, statusCode: StatusCodes.Status500InternalServerError);

    await using var conn = new SqlConnection(connString);
    await conn.OpenAsync(ct);

    var search = string.IsNullOrWhiteSpace(name) ? "%" : $"%{name}%";

    await using var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT TOP (50) Id, Name, Email FROM dbo.Users WHERE Name LIKE @name ORDER BY Id DESC";
    cmd.Parameters.Add(new SqlParameter("@name", System.Data.SqlDbType.NVarChar, 200) { Value = search });

    var users = new List<UserDto>();
    await using var reader = await cmd.ExecuteReaderAsync(ct);
    while (await reader.ReadAsync(ct))
    {
        users.Add(new UserDto(
            Id: reader.GetInt32(0),
            Name: reader.GetString(1),
            Email: reader.IsDBNull(2) ? null : reader.GetString(2)));
    }

    return Results.Ok(users);
});

app.MapGet("/api/users/{id:int}", async (int id, IConfiguration config, CancellationToken ct) =>
{
    var (connString, error) = BuildSqlConnectionString(config);
    if (error is not null) return Results.Problem(error, statusCode: StatusCodes.Status500InternalServerError);

    await using var conn = new SqlConnection(connString);
    await conn.OpenAsync(ct);

    await using var cmd = conn.CreateCommand();
    cmd.CommandText = "SELECT Id, Name, Email FROM dbo.Users WHERE Id = @id";
    cmd.Parameters.Add(new SqlParameter("@id", System.Data.SqlDbType.Int) { Value = id });

    await using var reader = await cmd.ExecuteReaderAsync(ct);
    if (!await reader.ReadAsync(ct)) return Results.NotFound();

    var user = new UserDto(
        Id: reader.GetInt32(0),
        Name: reader.GetString(1),
        Email: reader.IsDBNull(2) ? null : reader.GetString(2));

    return Results.Ok(user);
});

app.Run();

static (string? connString, string? error) BuildSqlConnectionString(IConfiguration config)
{
    var server = config["Sql:Server"];
    var database = config["Sql:Database"];
    var username = config["Sql:Username"];
    var password = config["Sql:Password"];

    if (string.IsNullOrWhiteSpace(server) ||
        string.IsNullOrWhiteSpace(database) ||
        string.IsNullOrWhiteSpace(username) ||
        string.IsNullOrWhiteSpace(password))
    {
        return (null, "SQL configuration is missing. Ensure Sql:Server/Database/Username/Password are set (typically via Key Vault secrets).");
    }

    var builder = new SqlConnectionStringBuilder
    {
        DataSource = server,
        InitialCatalog = database,
        UserID = username,
        Password = password,
        Encrypt = true,
        TrustServerCertificate = false,
        ConnectTimeout = 30,
    };

    return (builder.ConnectionString, null);
}

record UserDto(int Id, string Name, string? Email);

