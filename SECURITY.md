## 安全声明 / Security Notice（客户端截屏功能 / Client Screenshot Functionality）

本项目的客户端版本包含屏幕截取功能。为确保使用者充分了解相关安全与隐私风险，本项目特别做出如下声明：  
The client version of this project includes a screen capture feature. To ensure that users are fully aware of the associated security and privacy risks, this project makes the following statements:

- 客户端在运行过程中具备截取当前终端屏幕内容的能力，截屏内容可能包含敏感信息（例如聊天记录、个人隐私、账号密码、业务数据等）。  
  While the client is running, it is capable of capturing the content of the current terminal screen. Captured screenshots may contain sensitive information (for example, chat history, personal data, account credentials, or business data).

- 项目默认允许从服务端向客户端发起截图请求。  
  By default, the project allows the server to send screenshot requests to the client.

- 使用者可以通过将用户加入「允许列表」，使被加入允许列表的用户可以在**无需客户端当前使用者同意**的情况下，对该客户端进行屏幕截取操作并通知。  
  Users can add certain accounts to an "allow list". Accounts in the allow list can capture the client's screen and receive notifications **without the current client user explicitly approving each request**.

- 一旦你将用户加入允许列表，即表示您知悉并同意：被列入允许列表的主体在技术上具备在客户端前台/后台运行时获取屏幕内容的能力；若屏幕中包含第三方个人隐私或其他敏感信息，相关风险由部署方及实际使用者自行承担。  
  Once you add an account to the allow list, you acknowledge and agree that the party in the allow list is technically able to obtain the screen content while the client is running in the foreground or background. If the screen contains third‑party personal information or other sensitive data, all related risks are borne by the deployer and the actual user.

- 强烈建议在以下情形中谨慎使用或关闭截屏功能，或不将任何用户加入允许列表：  
  You are strongly advised to use this feature with caution or disable screenshot functionality and avoid adding any account to the allow list in the following situations:
  - 处理高敏感级别数据（如生产数据库、金融数据、医疗记录等）；  
    When handling highly sensitive data (such as production databases, financial data, or medical records);
  - 法律、合同或公司制度禁止截屏或远程监控的场景；  
    In scenarios where screenshots or remote monitoring are prohibited by law, contract, or internal policies;
  - 无法确保所有相关个人已被充分告知并取得合法授权的环境。  
    In environments where it is not possible to ensure that all affected individuals have been fully informed and have given valid consent.

- 在向他人分发本客户端或对他人设备进行部署时，您有责任向最终用户明确告知上述截屏能力及潜在风险，并在适用法律法规要求下获取必要的同意和授权。  
  When distributing this client to others or deploying it on other people's devices, you are responsible for clearly informing end users of the above screenshot capabilities and potential risks, and for obtaining all necessary consents and authorizations as required by applicable laws and regulations.

- 本项目以「现状」提供，项目作者不对因误用、非法使用或未充分告知用户而导致的任何合规、法律或经济损失承担责任。  
  This project is provided "as is". The author of the project is not responsible for any compliance issues, legal disputes, or economic losses caused by misuse, illegal use, or failure to properly inform users.

如您不同意上述内容，请勿在含有敏感信息的环境中启用客户端截屏功能，或在部署前关闭相关功能与配置。  
If you do not agree with the above statements, do not enable the client's screenshot functionality in environments that contain sensitive information, or disable the relevant features and configuration before deployment.



